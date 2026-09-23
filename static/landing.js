const q = s => document.querySelector(s);
const names = {structure_transformed:'Реорганизация',transferred:'Перенос функции',potential_loss:'Потенциальная потеря',possible_duplicate:'Возможное дублирование'};
const hints = {structure_transformed:'Изменилось подразделение',transferred:'Обязанность сохранилась',potential_loss:'Нужна проверка покрытия',possible_duplicate:'Два владельца — один объект'};
function escape(value='') { return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]); }
let result;
function showFinding(id) {
  const item=result.findings.find(f=>f.id===id); if(!item)return;
  q('#demo-kind').textContent=names[item.finding_type];
  q('#demo-finding-title').textContent=item.title;
  q('#demo-summary').textContent=item.summary;
  q('#demo-limit').textContent=item.limitations;
  for(const [key,source] of [['before',item.before_source],['after',item.after_source]]) {
    q(`#demo-${key}-label`).textContent=source ? (source.side==='before'?'До':'После') : (key==='before'?'До':'После');
    q(`#demo-${key}-meta`).textContent=source ? `${source.filename} · ${source.revision} · ${source.clause_label ? 'пункт '+source.clause_label : source.locator}` : 'Источник соответствия отсутствует';
    q(`#demo-${key}`).textContent=source?.original_text || 'Соответствие в этой стороне загруженного комплекта не найдено. Это не доказательство отсутствия обязанности в организации.';
  }
  document.querySelectorAll('[data-finding]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.finding===id)));
  q('#demo-evidence').classList.remove('hidden');
}
async function loadDemo() {
  q('#demo-error').classList.add('hidden'); q('#demo-loading').classList.remove('hidden');
  try {
    const response=await fetch('/demo/result.json'); if(!response.ok)throw new Error('demo'); result=await response.json();
    const items=Object.keys(names).map(type=>result.findings.find(f=>f.finding_type===type)).filter(Boolean);
    q('#demo-cases').innerHTML=items.map(f=>`<button type="button" class="demo-case" data-finding="${escape(f.id)}" aria-pressed="false"><strong>${names[f.finding_type]}</strong><span>${hints[f.finding_type]}</span></button>`).join('');
    document.querySelectorAll('[data-finding]').forEach(b=>b.addEventListener('click',()=>showFinding(b.dataset.finding)));
    if(items.length)showFinding(items[0].id);
    q('#demo-receipt').textContent=`Запуск ${result.run.id}. Режим: ${result.run.model_mode}; поиск: ${result.metadata.search_mode}. Алгоритм: ${result.metadata.algorithm_version}. SHA-256 входа: ${result.run.input_hash}.`;
  } catch {q('#demo-error').classList.remove('hidden');}
  finally {q('#demo-loading').classList.add('hidden');}
}
q('#retry-demo').addEventListener('click',loadDemo);
loadDemo();
