/* Conservative extraction: only labelled fields, JSON keys, or a standalone ID.
   Unknown positional barcode formats are deliberately not guessed. */
(function (root) {
  'use strict';
  const fold = s => String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toUpperCase();
  function date(value) {
    let s = fold(value).trim(), y, m, d;
    let hit = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
    if (hit) [,y,m,d] = hit;
    else {
      hit = s.match(/^(\d{1,2})[-/. ](\d{1,2}|[A-Z]+)[-/. ](\d{4})$/);
      if (!hit) return '';
      [,d,m,y] = hit;
      if (!/^\d+$/.test(m)) m = ['ENE','FEB','MAR','ABR','MAY','JUN','JUL','AGO','SEP','OCT','NOV','DIC'].indexOf(m.slice(0,3))+1;
    }
    const dt = new Date(Date.UTC(+y,+m-1,+d));
    if (+y < 1900 || dt.getUTCFullYear() !== +y || dt.getUTCMonth() !== +m-1 || dt.getUTCDate() !== +d || dt > new Date()) return '';
    return `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
  }
  function cedula(value) {
    const digits = String(value).replace(/[\s-]/g,'');
    return /^[0-9]{11}$/.test(digits) ? `${digits.slice(0,3)}-${digits.slice(3,10)}-${digits.slice(10)}` : '';
  }
  const labels = /^(NOMBRES?|APELLIDOS?|FECHA\s+DE\s+NACIMIENTO|NACIMIENTO|FECHA\s+NAC\.?|NUMERO\s+DE\s+CEDULA|CEDULA(?:\s+DE\s+IDENTIDAD(?:\s+Y\s+ELECTORAL)?)?|BIRTHDATE|FIRSTNAME|LASTNAME)\s*(?:[:=]\s*|\s+|$)/;
  function parse(raw) {
    const out = {}, warnings = [];
    const set = (label, value) => {
      label = fold(label).replace(/[ _-]/g,''); value = String(value || '').trim();
      let key, valid;
      if (['NOMBRE','NOMBRES','FIRSTNAME'].includes(label)) key='FirstName';
      if (['APELLIDO','APELLIDOS','LASTNAME'].includes(label)) key='LastName';
      if (['FECHADENACIMIENTO','FECHANAC.','NACIMIENTO','BIRTHDATE'].includes(label)) { key='BirthDate'; valid=date(value); }
      if (label.includes('CEDULA')) { key='Cedula'; valid=cedula(value); }
      if (!key || !value) return;
      if (key==='FirstName' || key==='LastName') valid = /^[\p{L}][\p{L}\s.'’\-]{0,79}$/u.test(value) ? value.replace(/\s+/g,' ') : '';
      if (!valid) { warnings.push('Un campo no pudo interpretarse; complételo manualmente.'); return; }
      if (out[key] && out[key]!==valid) { out[key]=''; warnings.push('Hay valores contradictorios: revise el documento.'); }
      else out[key]=valid;
    };
    raw = String(raw || '').slice(0,16000);
    try { const obj=JSON.parse(raw); if (obj && !Array.isArray(obj)) Object.entries(obj).forEach(([k,v])=>set(k,v)); } catch (_) { /* non-JSON */ }
    const lines = raw.replace(/[\x1c-\x1f|;]/g,'\n').split(/[\r\n]+/).map(s=>s.trim()).filter(Boolean);
    for (let i=0;i<lines.length;i++) {
      const hit=fold(lines[i]).match(labels);
      if (!hit) continue;
      let value=lines[i].slice(hit[0].length).trim();
      if (!value && lines[i+1] && !labels.test(fold(lines[i+1]))) value=lines[i+1];
      set(hit[1],value);
    }
    const ids=[...raw.matchAll(/(?<!\d)\d{3}[- ]?\d{7}[- ]?\d(?!\d)/g)].map(m=>cedula(m[0]));
    const unique=[...new Set(ids.filter(Boolean))];
    if (!out.Cedula && unique.length===1) out.Cedula=unique[0];
    if (unique.length>1) { out.Cedula=''; warnings.push('Se detectaron varias cédulas. Escríbala manualmente.'); }
    if (!Object.values(out).some(Boolean)) warnings.push('Formato no reconocido. Use el frente de la cédula o la entrada manual.');
    return { values:out, warnings:[...new Set(warnings)] };
  }
  const api={parse,date,cedula};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.CedulaParser=api;
})(typeof window === 'undefined' ? globalThis : window);
