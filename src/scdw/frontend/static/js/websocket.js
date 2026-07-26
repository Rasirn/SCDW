/* MACtrl 单连接 WebSocket 管理器。连接由 app.js 在事件处理器就绪后启动。 */
window.MACtrlSocket = (() => {
  let socket = null, state = 'idle', retryTimer = null, retryCount = 0, generation = 0, initError = '';
  const listeners = new Set();
  function notify() { listeners.forEach(listener => listener({ state, initError })); }
  function url() { const protocol=location.protocol==='https:'?'wss:':'ws:'; const host=location.host||`127.0.0.1:${window.BACKEND_PORT||17788}`; return `${protocol}//${host}/ws`; }
  function setState(value) { state=value; notify(); }
  function scheduleReconnect() { if (retryTimer || state==='init_error') return; const delay=Math.min(1000*2**retryCount,8000); retryCount++; retryTimer=setTimeout(()=>{retryTimer=null;connect()},delay); }
  function connect() {
    if (socket && (socket.readyState===WebSocket.CONNECTING||socket.readyState===WebSocket.OPEN)) return;
    if (retryTimer) { clearTimeout(retryTimer); retryTimer=null; }
    const currentGeneration=++generation; setState(retryCount?'reconnecting':'connecting');
    const candidate=new WebSocket(url()); socket=candidate;
    candidate.onopen=event=>{if(event.currentTarget!==socket||currentGeneration!==generation)return;setState('open')};
    candidate.onmessage=event=>{if(event.currentTarget!==socket||currentGeneration!==generation)return;let payload;try{payload=JSON.parse(event.data)}catch{window.Renderer?.diagnostic('协议','服务端返回了无效 JSON');return}if(payload.type==='ready'){retryCount=0;setState('ready')}else if(payload.type==='init_error'){initError=payload.message||'MACtrl 后端初始化失败';setState('init_error')}window.handleMACtrlEvent?.(payload);};
    candidate.onerror=event=>{if(event.currentTarget===socket)notify()};
    candidate.onclose=event=>{if(event.currentTarget!==socket||currentGeneration!==generation)return;socket=null;if(state!=='init_error'){setState('disconnected');window.handleMACtrlDisconnect?.();scheduleReconnect();}};
  }
  function send(payload) { if (!isReady()) return false; try { socket.send(JSON.stringify(payload)); return true } catch { setState('disconnected'); return false; } }
  function disconnect() { generation++; if(retryTimer)clearTimeout(retryTimer);retryTimer=null;if(socket){socket.close();socket=null}setState('disconnected'); }
  function isReady() { return state==='ready'&&socket?.readyState===WebSocket.OPEN; }
  window.addEventListener('beforeunload',disconnect,{once:true});
  function log(level, message, details) { return send({type:'client_log', level, message, details}); }
  window.addEventListener('error', event => log('error', event.message, {source:event.filename, line:event.lineno, column:event.colno}), true);
  window.addEventListener('unhandledrejection', event => log('error', 'unhandledrejection', {reason:String(event.reason)}));
  return { connect, disconnect, send, log, isReady, isOpen:()=>socket?.readyState===WebSocket.OPEN, getState:()=>state, onStateChange:fn=>{listeners.add(fn);return()=>listeners.delete(fn)}, buildUrl:url };
})();
