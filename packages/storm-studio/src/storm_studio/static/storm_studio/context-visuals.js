(() => {
 const create=(tag,text,parent)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(parent)parent.append(el);return el;};
 const svg=(parent,tag,attrs,text)=>{const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value]of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;parent.append(el);return el;};
 const color=label=>{let hash=0;for(const char of String(label))hash=(hash*31+char.charCodeAt(0))>>>0;return `hsl(${hash*137.508%360} 55% 45%)`;};
 const number=value=>typeof value==='number'&&Number.isFinite(value);
 function plot(canvas,points,{equal=false}={}){
  canvas.replaceChildren();points=points.filter(p=>number(p.x)&&number(p.y));
  if(!points.length){svg(canvas,'text',{x:25,y:45,fill:'#647085'},'No hay coordenadas declaradas y válidas para esta vista.');return;}
  let xmin=Math.min(...points.map(p=>p.x)),xmax=Math.max(...points.map(p=>p.x)),ymin=Math.min(...points.map(p=>p.y)),ymax=Math.max(...points.map(p=>p.y));
  let dx=xmax-xmin||1,dy=ymax-ymin||1;if(equal){const scale=Math.max(dx/680,dy/260);dx=scale*680;dy=scale*260;const cx=(xmin+xmax)/2,cy=(ymin+ymax)/2;xmin=cx-dx/2;ymin=cy-dy/2;}
  svg(canvas,'line',{x1:60,y1:310,x2:760,y2:310,stroke:'#b8c1d4'});svg(canvas,'line',{x1:60,y1:30,x2:60,y2:310,stroke:'#b8c1d4'});
  svg(canvas,'text',{x:60,y:338,'font-size':11},xmin.toFixed(2));svg(canvas,'text',{x:705,y:338,'font-size':11},(xmin+dx).toFixed(2));
  svg(canvas,'text',{x:5,y:40,'font-size':11},(ymin+dy).toFixed(2));svg(canvas,'text',{x:5,y:308,'font-size':11},ymin.toFixed(2));
  points.forEach(p=>{const x=65+(p.x-xmin)/dx*680,y=305-(p.y-ymin)/dy*260;const dot=svg(canvas,'circle',{cx:x,cy:y,r:p.name?5:3,fill:color(p.label),opacity:.8});svg(dot,'title',{},`${p.id??p.name??''} · ${p.label} · (${p.x}, ${p.y})`);if(p.name)svg(canvas,'text',{x:x+7,y:y-5,'font-size':11},p.name);});
 }
 function posePoints(row,names){const pairs=names.map((name,index)=>({name,index})).filter(p=>p.name.endsWith('_x'));return pairs.flatMap(p=>{const y=names.indexOf(p.name.slice(0,-2)+'_y');return y<0?[]:[{x:row.values[p.index],y:row.values[y],name:p.name.slice(0,-2),label:row.label,id:row.id}];});}
 window.StormContextVisuals={preview(result,parent){
  parent.replaceChildren();
  const raw=Object.values(result.outputs||{}).find(value=>value.source_preview)?.source_preview;
  for(const [title,ports]of [['Entrada',raw?{'Coordenadas de la fuente':raw}:result.inputs],['Salida',result.outputs]]){
   const article=create('article',undefined,parent);create('h3',title,article);
   for(const [port,context]of Object.entries(ports||{})){
    create('h4',port,article);const rows=context.rows||[];create('p',`${context.data?.length??context.data?.shape?.[0]??rows.length} observaciones · ${context.schema?.data?.units||context.metadata?.units||'unidades no declaradas'} · muestra, no validación completa`,article);
    const canvas=document.createElementNS('http://www.w3.org/2000/svg','svg');canvas.setAttribute('viewBox','0 0 800 360');canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title+' del contexto');article.append(canvas);
    const first=rows[0],names=context.metadata?.feature_names||[];let points=[],poseRow=first;
    if(Array.isArray(first?.[0])){const identity=context.metadata?.observation_ids?.[0],parents=context.window_parents?.[0]||[],anchor=parents.indexOf(identity);poseRow=anchor>=0?first[anchor]:[];}
    const isPose=names.some(name=>name.endsWith('_x'));
    if(isPose&&Array.isArray(poseRow))points=posePoints({values:poseRow,label:port},names);
    else if(Array.isArray(first)&&!Array.isArray(first[0]))points=rows.map((row,i)=>({x:i,y:row[0],label:port,id:context.metadata?.observation_ids?.[i]}));
    else if(number(first))points=rows.map((row,i)=>({x:i,y:row,label:port}));
    else if(Array.isArray(poseRow))points=poseRow.map((value,i)=>({x:i,y:value,label:port}));
    plot(canvas,points,{equal:isPose});create('p',isPose?`Pose de ${context.metadata?.observation_ids?.[0]??'la muestra'} · ejes X/Y con escala uniforme. Cada panel conserva sus unidades y sistema de coordenadas.`:'Índice de observación y primera variable; en ventanas, variables del frame de referencia declarado.',article).className='context-plot-status';
    for(const audit of context.preparation_audit||[])create('p',`${audit.session}: ${audit.frames} frames inspeccionados · ${audit.windows} ventanas · inválidos por confianza: ${audit.invalid_after_likelihood} · inválidos tras control espacial: ${audit.invalid_after_spatial}`,article);
    const wrap=create('div',undefined,article);wrap.className='table-scroll';const table=create('table',undefined,wrap);rows.forEach((row,i)=>{const tr=create('tr',undefined,table);create('th',context.metadata?.observation_ids?.[i]??i,tr);create('td',JSON.stringify(row),tr);});
   }
   if(!Object.keys(ports||{}).length)create('p','No hay salida disponible. Consultá el diagnóstico del nodo.',article);
  }
 }};
 const source=document.getElementById('context-visual-data');if(!source)return;
 const data=JSON.parse(source.textContent),geometry=data.geometry||[],pose=data.pose||[];
 const x=document.getElementById('geometry-x'),y=document.getElementById('geometry-y');const dimensions=Math.max(0,...geometry.map(row=>row.values?.length||0));
 for(let i=0;i<dimensions;i++){for(const select of [x,y]){const option=create('option',data.geometry_kind==='Espacio latente'?`Latente ${i+1}`:data.features[i]||`Variable ${i+1}`,select);option.value=i;}}
 y.value=dimensions>1?'1':'0';const drawGeometry=()=>plot(document.getElementById('result-geometry'),geometry.map(row=>({x:row.values[Number(x.value)],y:row.values[Number(y.value)],label:row.label,id:row.id})));x.onchange=y.onchange=drawGeometry;drawGeometry();
 document.getElementById('geometry-description').textContent=`${data.geometry_kind} · ${data.sample_count} de ${data.total_count} salidas válidas; muestra determinista para el gráfico. La distribución utiliza todas las salidas válidas.`;
 const slider=document.getElementById('result-frame');slider.max=Math.max(0,pose.length-1);slider.disabled=!pose.length;
 const drawPose=()=>{const row=pose[Number(slider.value)];if(!row)return;plot(document.getElementById('result-pose'),posePoints(row,data.features),{equal:true});document.getElementById('pose-readout').textContent=`${row.id} · sesión ${row.session??'no declarada'} · frame ${row.frame??'no declarado'} · etiqueta ${row.label} · referencia ${row.target??'sin etiqueta'} · ${data.units||'unidades no declaradas'}`;};slider.oninput=drawPose;drawPose();
 const timeline=document.getElementById('result-label-timeline');pose.forEach((row,i)=>{const rect=svg(timeline,'rect',{x:i/pose.length*800,y:8,width:Math.max(1,800/pose.length),height:28,fill:color(row.label),tabindex:0,role:'button','aria-label':`${row.id}: ${row.label}`});svg(rect,'title',{},`${row.id}: ${row.label}`);const select=()=>{slider.value=i;drawPose();};rect.onclick=select;rect.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}};});
})();
