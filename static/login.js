document.querySelector('#login-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button[type=submit]');button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Входим…';
  const error=document.querySelector('#login-error');error.textContent='';
  try {
    const response=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.querySelector('#password').value})});
    const result=await response.json();if(!response.ok)throw new Error(result.detail||'Не удалось войти');
    document.querySelector('#password').value='';location.assign('/workspace');
  } catch(e) {error.textContent=e.message;button.disabled=false;button.removeAttribute('aria-busy');button.textContent='Войти';document.querySelector('#password').setAttribute('aria-invalid','true');document.querySelector('#password').focus();}
});
document.querySelector('#show-password').addEventListener('click',event=>{const input=document.querySelector('#password');input.type=input.type==='password'?'text':'password';event.currentTarget.textContent=input.type==='password'?'Показать код':'Скрыть код';});
