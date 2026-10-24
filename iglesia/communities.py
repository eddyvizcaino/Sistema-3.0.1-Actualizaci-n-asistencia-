"""Módulo independiente COMUNIDADES DE ORACIÓN."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from .db import audit, get_db, now_iso
from .security import clean, pastoral_required

bp=Blueprint('communities',__name__)

def _system_ministry_id():
    """Compatibilidad de BD: la relación técnica no se muestra como ministerio funcional."""
    db=get_db()
    row=db.execute("SELECT Id FROM Ministries WHERE Name=? ORDER BY Id LIMIT 1",('__COMUNIDADES_ORACION__',)).fetchone()
    if row:return row['Id']
    # Reutiliza el ministerio técnico legado solo si existe y no tiene miembros/usuarios.
    row=db.execute("SELECT Id FROM Ministries WHERE lower(Name) IN (lower('Ministerio de Oración'),lower('Oración'),lower('Oracion')) ORDER BY Id LIMIT 1").fetchone()
    if row:
        mid=row['Id']
        used=db.execute('SELECT (SELECT COUNT(*) FROM Members WHERE MinistryId=? AND DeletedAt IS NULL)+(SELECT COUNT(*) FROM Users WHERE MinistryId=?)+(SELECT COUNT(*) FROM Events WHERE MinistryId=?)',(mid,mid,mid)).fetchone()[0]
        if not used:
            db.execute("UPDATE Ministries SET Name=? WHERE Id=?",('__COMUNIDADES_ORACION__',mid)); db.commit(); return mid
    cur=db.execute("INSERT INTO Ministries(Name,Description,CreatedAt) VALUES(?,?,?)",('__COMUNIDADES_ORACION__','Registro técnico interno; no mostrar en Ministerios',now_iso()))
    db.commit(); return cur.lastrowid

def _community(cid):
    c=get_db().execute('SELECT * FROM Communities WHERE Id=?',(cid,)).fetchone()
    if not c: abort(404)
    return c

def _all_members():
    return get_db().execute("SELECT Id,MemberCode,FirstName,LastName,FullName,Phone,Status FROM Members WHERE DeletedAt IS NULL ORDER BY FirstName,LastName,FullName").fetchall()

def _valid_people(*ids):
    ids=[x for x in ids if x]
    if len(ids)!=len(set(ids)): return False
    db=get_db(); return all(db.execute('SELECT 1 FROM Members WHERE Id=? AND DeletedAt IS NULL',(x,)).fetchone() for x in ids)

def _sync_members(db,cid,selected):
    selected=set(selected)
    valid={r['Id'] for r in db.execute('SELECT Id FROM Members WHERE DeletedAt IS NULL').fetchall()}
    selected &= valid
    active={r['MemberId'] for r in db.execute('SELECT MemberId FROM CommunityMembers WHERE CommunityId=? AND Active=1',(cid,)).fetchall()}
    for mid in active-selected:
        db.execute('UPDATE CommunityMembers SET Active=0,LeftAt=? WHERE CommunityId=? AND MemberId=? AND Active=1',(now_iso(),cid,mid))
    for mid in selected-active:
        old=db.execute('SELECT Id FROM CommunityMembers WHERE CommunityId=? AND MemberId=? ORDER BY Id DESC',(cid,mid)).fetchone()
        if old: db.execute('UPDATE CommunityMembers SET Active=1,JoinedAt=?,LeftAt=NULL WHERE Id=?',(now_iso(),old['Id']))
        else: db.execute('INSERT INTO CommunityMembers(CommunityId,MemberId,Active,JoinedAt) VALUES(?,?,1,?)',(cid,mid,now_iso()))

def _selected(cid):
    if not cid:return set()
    return {r['MemberId'] for r in get_db().execute('SELECT MemberId FROM CommunityMembers WHERE CommunityId=? AND Active=1',(cid,)).fetchall()}

@bp.route('/communities')
@pastoral_required
def index():
    db=get_db(); mid=_system_ministry_id()
    rows=db.execute('''SELECT c.*,COALESCE(l1.FirstName||' '||l1.LastName,l1.FullName,'') Leader1Name,
      COALESCE(l2.FirstName||' '||l2.LastName,l2.FullName,'') Leader2Name,
      SUM(CASE WHEN cm.Active=1 THEN 1 ELSE 0 END) Total FROM Communities c
      LEFT JOIN Members l1 ON l1.Id=c.LeaderMemberId LEFT JOIN Members l2 ON l2.Id=c.Leader2MemberId
      LEFT JOIN CommunityMembers cm ON cm.CommunityId=c.Id WHERE c.MinistryId=?
      GROUP BY c.Id,l1.Id,l2.Id ORDER BY c.Name''',(mid,)).fetchall()
    return render_template('communities.html',title='COMUNIDADES DE ORACIÓN',rows=rows,can_manage=True)

@bp.route('/communities/new',methods=['GET','POST'])
@pastoral_required
def new():
    db=get_db(); mid=_system_ministry_id()
    if request.method=='POST':
        name=clean(request.form.get('Name'),200); status=request.form.get('Status') if request.form.get('Status') in ('Activa','Inactiva') else 'Activa'; l1=request.form.get('LeaderMemberId',type=int); l2=request.form.get('Leader2MemberId',type=int)
        if not name: flash('El nombre de la comunidad es obligatorio.')
        elif not _valid_people(l1,l2): flash('Líder 1 y Líder 2 deben ser miembros válidos y diferentes.')
        elif db.execute('SELECT 1 FROM Communities WHERE MinistryId=? AND lower(Name)=lower(?)',(mid,name)).fetchone(): flash('Ya existe una comunidad de oración con ese nombre.')
        else:
            cur=db.execute('INSERT INTO Communities(MinistryId,Name,LeaderMemberId,Leader2MemberId,Status,CreatedDate,Notes,CreatedAt) VALUES(?,?,?,?,?,?,?,?)',(mid,name,l1,l2,status,request.form.get('CreatedDate') or None,clean(request.form.get('Notes'),2000),now_iso()))
            _sync_members(db,cur.lastrowid,[int(x) for x in request.form.getlist('member_ids') if x.isdigit()]); audit('Crear','Comunidad de Oración',cur.lastrowid,f'Comunidad {name}'); db.commit(); flash('Comunidad de oración creada.'); return redirect(url_for('communities.index'))
    return render_template('community_form.html',title='CREAR COMUNIDAD DE ORACIÓN',c={},members=_all_members(),selected=set())

@bp.route('/communities/<int:cid>/edit',methods=['GET','POST'])
@pastoral_required
def edit(cid):
    c=_community(cid); db=get_db(); mid=_system_ministry_id()
    if c['MinistryId']!=mid: abort(404)
    if request.method=='POST':
        name=clean(request.form.get('Name'),200); status=request.form.get('Status') if request.form.get('Status') in ('Activa','Inactiva') else 'Activa'; l1=request.form.get('LeaderMemberId',type=int); l2=request.form.get('Leader2MemberId',type=int)
        if not name: flash('El nombre de la comunidad es obligatorio.')
        elif not _valid_people(l1,l2): flash('Líder 1 y Líder 2 deben ser miembros válidos y diferentes.')
        elif db.execute('SELECT 1 FROM Communities WHERE MinistryId=? AND lower(Name)=lower(?) AND Id<>?',(mid,name,cid)).fetchone(): flash('Ya existe otra comunidad con ese nombre.')
        else:
            db.execute('UPDATE Communities SET Name=?,LeaderMemberId=?,Leader2MemberId=?,Status=?,CreatedDate=?,Notes=?,UpdatedAt=? WHERE Id=?',(name,l1,l2,status,request.form.get('CreatedDate') or None,clean(request.form.get('Notes'),2000),now_iso(),cid)); _sync_members(db,cid,[int(x) for x in request.form.getlist('member_ids') if x.isdigit()]); audit('Editar','Comunidad de Oración',cid,f'Comunidad actualizada: {name}'); db.commit(); flash('Comunidad actualizada.'); return redirect(url_for('communities.view',cid=cid))
    return render_template('community_form.html',title='EDITAR COMUNIDAD DE ORACIÓN',c=c,members=_all_members(),selected=_selected(cid))

@bp.route('/communities/<int:cid>',methods=['GET','POST'])
@pastoral_required
def view(cid):
    c=_community(cid); db=get_db()
    if c['MinistryId']!=_system_ministry_id(): abort(404)
    if request.method=='POST':
        member_id=request.form.get('member_id',type=int); member=db.execute('SELECT Id FROM Members WHERE Id=? AND DeletedAt IS NULL',(member_id,)).fetchone()
        if not member: flash('Miembro no encontrado.')
        elif db.execute('SELECT Id FROM CommunityMembers WHERE CommunityId=? AND MemberId=? AND Active=1',(cid,member_id)).fetchone(): flash('Ese miembro ya pertenece a esta comunidad.')
        else: _sync_members(db,cid,list(_selected(cid)|{member_id})); db.commit(); flash('Miembro agregado a la comunidad.')
    people=db.execute('''SELECT cm.Id cmid,m.Id,m.MemberCode,m.FirstName,m.LastName,m.FullName,m.Phone FROM CommunityMembers cm JOIN Members m ON m.Id=cm.MemberId WHERE cm.CommunityId=? AND cm.Active=1 AND m.DeletedAt IS NULL ORDER BY m.FirstName,m.LastName,m.FullName''',(cid,)).fetchall()
    ministry=db.execute('SELECT Id,Name FROM Ministries WHERE Id=?',(c['MinistryId'],)).fetchone()
    leader1=db.execute('SELECT MemberCode,FirstName,LastName,FullName FROM Members WHERE Id=?',(c['LeaderMemberId'],)).fetchone() if c['LeaderMemberId'] else None
    leader2=db.execute('SELECT MemberCode,FirstName,LastName,FullName FROM Members WHERE Id=?',(c['Leader2MemberId'],)).fetchone() if c['Leader2MemberId'] else None
    return render_template('community_view.html',title=c['Name'],c=c,people=people,members=_all_members(),can_manage=True,ministry=ministry,leader1=leader1,leader2=leader2)

@bp.route('/communities/<int:cid>/members/<int:member_id>/remove',methods=['POST'])
@pastoral_required
def remove_member(cid,member_id):
    _community(cid); db=get_db(); db.execute('UPDATE CommunityMembers SET Active=0,LeftAt=? WHERE CommunityId=? AND MemberId=? AND Active=1',(now_iso(),cid,member_id)); audit('Retirar miembro','Comunidad de Oración',cid,f'Miembro {member_id} retirado'); db.commit(); flash('Miembro retirado; su ficha permanece intacta.'); return redirect(url_for('communities.view',cid=cid))
