(() => {
  'use strict';
  const $=id=>document.getElementById(id), tools=$('identity-tools');
  if (!tools) return;
  const form=tools.closest('form'), fields=['FirstName','LastName','BirthDate','Cedula'];
  const input=k=>form.elements.namedItem(k), status=$('scan-status'), duplicate=$('duplicate-status');
  const save=$('save-member'), video=$('scan-video'), dialog=$('scan-dialog'), review=$('review-dialog');
  let stream=null, worker=null, generation=0, debounce, scanTimer, query=0, submitting=false;
  function stopCamera() { if(stream) stream.getTracks().forEach(t=>t.stop()); stream=null; video.srcObject=null; clearTimeout(scanTimer); }
  function clearReview() { fields.forEach(k=>$('review-'+k).value=''); $('review-warnings').textContent=''; }
  async function cancelScan() {
    generation++; stopCamera(); if(dialog.open) dialog.close();
    const w=worker; worker=null; if(w) await w.terminate();
    $('capture-front').disabled=false;
  }
  function showDetected(text) {
    const result=CedulaParser.parse(text); clearReview();
    fields.forEach(k=>$('review-'+k).value=result.values[k] || '');
    const missing=fields.filter(k=>!result.values[k]).length;
    $('review-warnings').textContent=result.warnings.join(' ') + (missing ? ' Faltan datos: use el frente o complételos manualmente.' : 'Compare estos datos con la cédula.');
    review.showModal(); status.textContent='Lectura terminada. Revise los valores detectados.';
  }
  async function checkDuplicate() {
    const id=++query, value=input('Cedula').value;
    if(!value.trim()) { duplicate.replaceChildren(); save.disabled=false; return true; }
    save.disabled=true; duplicate.textContent='Comprobando cédula…';
    try {
      const response=await fetch('/api/members/check-cedula', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':form.elements.csrf_token.value}, body:JSON.stringify({cedula:value,exclude:tools.dataset.memberId || null})});
      if(!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('check');
      const result=await response.json(); if(id!==query) return false;
      duplicate.replaceChildren(); save.disabled=!!result.exists;
      if(result.exists) {
        duplicate.append(document.createTextNode(result.message+(result.deleted ? ' Está en la papelera.' : '')+' '));
        if(result.url) { const a=document.createElement('a'); a.href=result.url; a.textContent='ABRIR PERFIL EXISTENTE'; duplicate.append(a); }
      } else duplicate.textContent='No hay otra ficha con esta cédula.';
      return !result.exists;
    } catch (_) {
      if(id===query) { duplicate.textContent='No se pudo comprobar ahora. Al guardar, el servidor verificará los duplicados.'; save.disabled=false; }
      return id===query; // server is authoritative
    }
  }
  input('Cedula').addEventListener('input',()=>{ query++; save.disabled=true; clearTimeout(debounce); debounce=setTimeout(checkDuplicate,300); });
  input('Cedula').addEventListener('change',checkDuplicate);
  // Avoid accidental form submission from a keyboard-wedge scanner.
  input('Cedula').addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault(); showDetected(input('Cedula').value);} });
  function readTera() { const text=$('tera-code').value; $('tera-code').value=''; if(text.trim()) showDetected(text); }
  $('read-tera').onclick=readTera;
  let teraTimer;
  $('tera-code').addEventListener('keydown',e=>{ if(e.key==='Enter' || e.key==='Tab'){e.preventDefault(); clearTimeout(teraTimer); teraTimer=setTimeout(readTera,180);} });
  $('tera-code').addEventListener('input',()=>{if(teraTimer){clearTimeout(teraTimer);teraTimer=setTimeout(readTera,180);}});
  $('apply-detected').onclick=()=>{
    const birth=$('review-BirthDate').value;
    if(birth && !CedulaParser.date(birth)) { $('review-warnings').textContent='Revise la fecha de nacimiento: debe ser válida y no futura.'; return; }
    fields.forEach(k=>{ const value=$('review-'+k).value.trim(); if(value) input(k).value=value; });
    review.close(); clearReview(); status.textContent='Datos aplicados. Puede corregirlos en el formulario antes de GUARDAR.'; checkDuplicate();
  };
  $('cancel-review').onclick=()=>{review.close();clearReview();};
  review.addEventListener('cancel',clearReview);
  async function loadOCR() {
    if(window.Tesseract) return;
    await new Promise((resolve,reject)=>{
      const s=document.createElement('script'); s.src='https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/tesseract.min.js'; s.onload=resolve; s.onerror=reject;
      document.head.append(s);
    });
  }
  async function openCamera(mode) {
    await cancelScan(); const mine=generation;
    $('scan-title').textContent=mode==='ocr' ? 'ESCANEAR FRENTE DE CÉDULA' : 'LEER CÓDIGO DE BARRAS';
    $('capture-front').hidden=mode!=='ocr';
    if(!navigator.mediaDevices?.getUserMedia) {status.textContent='La cámara necesita HTTPS o localhost y un navegador compatible. Puede usar el lector Tera o la entrada manual.';return;}
    dialog.showModal(); status.textContent='Solicitando acceso a la cámara…';
    try {
      const acquired=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false});
      if(mine!==generation){acquired.getTracks().forEach(t=>t.stop());return;}
      stream=acquired;video.srcObject=stream;await video.play();
      status.textContent='Cámara lista. Encuadre la cédula.';
      scanTimer=setTimeout(()=>{cancelScan();status.textContent='La cámara se cerró por tiempo de espera. Puede intentar otra vez.';},60000);
      if(mode==='barcode') {
        if(!window.BarcodeDetector) throw new Error('barcode');
        const detector=new BarcodeDetector();
        const tick=async()=>{
          if(mine!==generation) return;
          try {
            const codes=await detector.detect(video);
            if(mine!==generation)return;
            if(codes.length){const raw=codes[0].rawValue;await cancelScan();showDetected(raw);return;}
            setTimeout(tick,200);
          }catch(_){await cancelScan();status.textContent='No se pudo leer el código con la cámara. Use el Tera, el frente o la entrada manual.';}
        }; tick();
      }
    }catch(_){if(mine===generation){await cancelScan();status.textContent='No se pudo abrir el lector de cámara. Revise los permisos; puede usar el Tera o la entrada manual.';}}
  }
  $('scan-front').onclick=()=>openCamera('ocr');
  $('scan-barcode').onclick=()=>openCamera('barcode');
  $('cancel-scan').onclick=()=>{cancelScan();status.textContent='Lectura cancelada. Puede completar los campos manualmente.';};
  dialog.addEventListener('cancel',e=>{e.preventDefault();$('cancel-scan').click();});
  $('capture-front').onclick=async()=>{
    if(!video.videoWidth)return;
    const mine=generation, canvas=document.createElement('canvas');
    const scale=Math.min(1,2200/video.videoWidth);canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);
    canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);stopCamera();
    $('capture-front').disabled=true;status.textContent='Preparando reconocimiento de texto…';
    let activeWorker;
    try {
      await loadOCR();if(mine!==generation)return;
      activeWorker=await Tesseract.createWorker('spa',1,{logger:m=>{if(mine===generation)status.textContent='Leyendo texto… '+Math.round((m.progress||0)*100)+'%';}});
      if(mine!==generation){await activeWorker.terminate();activeWorker=null;return;}
      worker=activeWorker;
      const result=await worker.recognize(canvas);
      if(mine===generation){dialog.close();showDetected(result.data.text);}
    }catch(_){if(mine===generation){dialog.close();status.textContent='No se pudo leer la imagen. Compruebe Internet, mejore la luz o complete los datos manualmente.';}}
    finally{canvas.width=canvas.height=0;if(activeWorker && worker===activeWorker){worker=null;await activeWorker.terminate();} $('capture-front').disabled=false;}
  };
  form.addEventListener('submit',async e=>{
    if(submitting)return;e.preventDefault();
    if(review.open || dialog.open)return;
    clearTimeout(debounce);
    if(await checkDuplicate()){submitting=true;form.requestSubmit();}
  });
  window.addEventListener('pagehide',()=>{cancelScan();clearReview();$('tera-code').value='';});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)cancelScan();});
  if(input('Cedula').value)checkDuplicate();
})();
