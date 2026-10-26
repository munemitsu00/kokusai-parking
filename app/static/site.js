(function(){
  // Prefill a two-hour daytime example without forcing a server-side timezone conversion.
  const form=document.getElementById('estimateForm');
  if(form){const a=form.querySelector('[name="arrive"]'),b=form.querySelector('[name="leave"]');
    if(!a.value&&!b.value){const now=new Date();const jpn=new Date(now.toLocaleString('en-US',{timeZone:'Asia/Tokyo'}));
      jpn.setMinutes(0,0,0);jpn.setHours(jpn.getHours()+1);
      const local=x=>`${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}T${String(x.getHours()).padStart(2,'0')}:${String(x.getMinutes()).padStart(2,'0')}`;
      a.value=local(jpn);jpn.setHours(jpn.getHours()+2);b.value=local(jpn);
    }
  }
})();
