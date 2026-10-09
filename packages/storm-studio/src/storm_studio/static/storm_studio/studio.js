const summary=document.querySelector('[data-execution-summary]');
if(summary&&document.body.dataset.screen!=='jobs'){
 const refresh=async()=>{try{const response=await fetch(summary.dataset.statusUrl);if(response.ok){const data=await response.json();summary.textContent=data.summary.label;}}catch{}};
 setInterval(()=>{if(!document.hidden)refresh();},15000);
}
document.querySelectorAll('[data-experiment-action]').forEach(form=>form.addEventListener('submit',async event=>{
 event.preventDefault();const button=form.querySelector('button'),status=document.getElementById('result-status');button.disabled=true;
 try{const payload=Object.fromEntries(new FormData(form));delete payload.csrfmiddlewaretoken;
 const response=await fetch(form.action,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify(payload)});
 const result=await response.json();if(!response.ok)throw Error(result.error);
 if(result.selection)location.reload();else status.textContent=`Trabajo encolado: ${result.job}. Consultá Ejecuciones y recargá esta vista al terminar.`;
 }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
}));
