window.connectMACtrl=()=>{let ws=new WebSocket(`ws://${location.host}/ws`);window.ws=ws;ws.onmessage=e=>window.handleEvent(JSON.parse(e.data));ws.onclose=()=>setTimeout(connectMACtrl,1500)};
