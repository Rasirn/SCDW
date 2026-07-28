const logoPlaceholder = document.querySelector('.brand svg');
if (logoPlaceholder) {
  const logo = document.createElement('img');
  Object.assign(logo, { className: 'brand-logo', src: '/assets/logo/mac_logo.png', alt: 'MACtrl Logo', width: 60, height: 60 });
  Object.assign(logo.style, { objectFit: 'contain', display: 'block' });
  logoPlaceholder.replaceWith(logo);
}

const input = document.querySelector('#input');
const send = document.querySelector('#send');
const stop = document.querySelector('#stop');
const resend = document.querySelector('#resend');
const statusPanel = document.querySelector('#run-status');
const statusStage = document.querySelector('#run-stage');
const statusTool = document.querySelector('#run-tool');
const turnElapsed = document.querySelector('#turn-elapsed');
const toolElapsed = document.querySelector('#tool-elapsed');
const lastActivity = document.querySelector('#last-activity');

window.MACTRL_BUILD_ID = 'phase1-status-1';
console.info(`[MACtrl] frontend build ${window.MACTRL_BUILD_ID}`);

const conversationState = {
  activeTurnId: null,
  status: 'idle',
  stage: '',
  startedAt: 0,
  finishedAt: 0,
  lastEventAt: 0,
  currentTool: null,
  lastToolElapsedMs: null,
  ticker: null,
  hideTimer: null,
  lastUserText: '',
  disconnected: false
};
let mode = localStorage.getItem('mactrl-mode') || 'thinking';

const duration = milliseconds => Renderer.formatDuration(milliseconds);
const relative = milliseconds => {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds < 2 ? '刚刚' : seconds < 60 ? `${seconds} 秒前` : `${Math.floor(seconds / 60)} 分钟前`;
};
const isRunning = () => Boolean(conversationState.activeTurnId);

function setBusy(value) {
  send.disabled = value;
  input.disabled = value;
  stop.hidden = !value;
  document.querySelectorAll('[data-mode]').forEach(button => { button.disabled = value; });
}
function clearTimers() {
  if (conversationState.ticker) clearInterval(conversationState.ticker);
  if (conversationState.hideTimer) clearTimeout(conversationState.hideTimer);
  conversationState.ticker = null;
  conversationState.hideTimer = null;
}
function renderRunStatus(now = Date.now()) {
  statusPanel.hidden = false;
  const running = isRunning();
  statusPanel.dataset.terminal = String(!running);
  statusPanel.dataset.status = conversationState.status;
  statusStage.textContent = conversationState.stage || '正在处理';
  statusTool.textContent = conversationState.currentTool ? `当前工具：${conversationState.currentTool.name}` : '';
  const end = conversationState.finishedAt || now;
  turnElapsed.textContent = duration(conversationState.startedAt ? end - conversationState.startedAt : 0);
  if (conversationState.currentTool) {
    const toolMs = Math.max(conversationState.currentTool.elapsedMs || 0, now - conversationState.currentTool.startedAt);
    toolElapsed.textContent = duration(toolMs);
  } else toolElapsed.textContent = conversationState.lastToolElapsedMs == null ? '--:--' : duration(conversationState.lastToolElapsedMs);
  lastActivity.textContent = conversationState.lastEventAt ? relative(now - conversationState.lastEventAt) : '刚刚';
  Renderer.tick(now);
}
function beginTurn(id, userText) {
  clearTimers();
  Object.assign(conversationState, {
    activeTurnId: id, status: 'running', stage: '正在分析需求', startedAt: Date.now(),
    finishedAt: 0, lastEventAt: Date.now(), currentTool: null, lastToolElapsedMs: null,
    lastUserText: userText, disconnected: false
  });
  setBusy(true); stop.disabled = false; stop.textContent = '停止'; resend.hidden = true;
  renderRunStatus();
  conversationState.ticker = setInterval(() => renderRunStatus(), 250);
}
function finishTurn(id, status, details = {}) {
  if (id && conversationState.activeTurnId && id !== conversationState.activeTurnId) return;
  if (id && !conversationState.activeTurnId) return;
  const labels = {
    complete: '已完成', cancelled: '已停止', failed: '执行失败', disconnected: '连接已中断，本轮可能未完成', cleared: ''
  };
  clearTimers();
  conversationState.finishedAt = Date.now();
  conversationState.lastEventAt = Date.now();
  conversationState.status = status;
  conversationState.stage = details.message || labels[status] || status;
  if (conversationState.currentTool) {
    conversationState.lastToolElapsedMs = conversationState.currentTool.elapsedMs || (Date.now() - conversationState.currentTool.startedAt);
  }
  conversationState.currentTool = null;
  conversationState.activeTurnId = null;
  setBusy(false); stop.disabled = false; stop.textContent = '停止';
  if (status === 'disconnected') {
    if (!input.value) input.value = conversationState.lastUserText;
    resend.hidden = !conversationState.lastUserText;
  }
  if (status === 'cleared') statusPanel.hidden = true;
  else {
    renderRunStatus();
    conversationState.hideTimer = setTimeout(() => {
      if (!isRunning() && conversationState.status === status) statusPanel.hidden = true;
    }, 8000);
  }
  input.focus();
}
function touchEvent() {
  conversationState.lastEventAt = Date.now();
}
function showConnectionStatus(message, status, hideAfter = 0) {
  if (isRunning()) return;
  clearTimers();
  Object.assign(conversationState, { status, stage: message, startedAt: 0, finishedAt: 0, lastEventAt: Date.now(), currentTool: null });
  renderRunStatus();
  if (hideAfter) conversationState.hideTimer = setTimeout(() => { if (!isRunning() && conversationState.status === status) statusPanel.hidden = true; }, hideAfter);
}

function go() {
  const userText = input.value.trim();
  if (!userText || isRunning()) return;
  if (!window.MACtrlSocket.isReady()) {
    conversationState.lastUserText = userText;
    Renderer.diagnostic('连接', 'MACtrl 服务尚未就绪，请检查连接状态。');
    return;
  }
  const id = crypto.randomUUID();
  if (!window.MACtrlSocket.send({ type: 'query', content: userText, mode, turn_id: id })) {
    conversationState.lastUserText = userText;
    Renderer.diagnostic('连接', '消息发送失败，正在重新连接 MACtrl 服务。');
    return;
  }
  Renderer.beginTurn(id, { userText, mode });
  beginTurn(id, userText);
  document.querySelector('#welcome')?.remove();
  input.value = '';
}

send.onclick = go;
resend.onclick = () => { input.value = conversationState.lastUserText; resend.hidden = true; go(); };
stop.onclick = () => {
  if (!conversationState.activeTurnId || stop.disabled) return;
  stop.disabled = true; stop.textContent = '正在停止…';
  conversationState.status = 'stopping'; conversationState.stage = '正在停止'; conversationState.lastEventAt = Date.now();
  renderRunStatus();
  if (!window.MACtrlSocket.send({ type: 'cancel', turn_id: conversationState.activeTurnId })) {
    const id = conversationState.activeTurnId;
    Renderer.disconnectTurn(id); finishTurn(id, 'disconnected');
  }
};
input.onkeydown = event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); if (!isRunning()) go(); }
};

document.querySelectorAll('[data-mode]').forEach(button => {
  button.onclick = () => {
    if (isRunning()) return;
    mode = button.dataset.mode; localStorage.setItem('mactrl-mode', mode);
    document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('selected', item === button));
    document.querySelector('#mode-help').textContent = mode === 'fast' ? '响应更快，适合简单任务' : '进行深入分析，适合复杂 PLC 任务';
  };
});
document.querySelector(`[data-mode=${mode}]`).click();
document.querySelector('#theme').onclick = () => { localStorage.setItem('mactrl-theme', document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); applyTheme(); };
document.querySelector('#clear').onclick = () => window.MACtrlSocket.send({ type: 'clear' });
document.querySelectorAll('.suggestions button').forEach(button => { button.onclick = () => { input.value = button.textContent; input.focus(); }; });

function showWelcome() {
  const welcome = document.createElement('div'); welcome.id = 'welcome';
  welcome.innerHTML = '<h1>MACtrl</h1><p>TIA 智控助手</p><p>让 PLC 开发更清晰、更高效、更可控。</p>';
  document.querySelector('#messages').append(welcome);
}

window.handleMACtrlEvent = event => {
  const id = event.turn_id;
  const lateTurnEvent = id && conversationState.activeTurnId !== id && ['turn_status', 'tool_progress', 'tool_result', 'turn_end', 'cancelled', 'stream_error'].includes(event.type);
  if (lateTurnEvent) return;
  if (id && !Renderer.hasTurn(id) && event.type !== 'turn_start') {
    Renderer.diagnostic(id, '收到未知回合事件，已隔离'); return;
  }
  touchEvent();
  switch (event.type) {
    case 'ready': break;
    case 'init_error': Renderer.diagnostic('初始化', event.message); break;
    case 'turn_start': break;
    case 'turn_status':
      if (id === conversationState.activeTurnId) {
        conversationState.stage = event.message || conversationState.stage;
        if (event.stage === 'cancelled') conversationState.status = 'stopping';
        renderRunStatus();
      }
      break;
    case 'reasoning_start': conversationState.stage = '正在分析需求'; Renderer.beginReasoning(id, event.round); break;
    case 'reasoning_delta': Renderer.appendReasoning(id, event.round, event.content); break;
    case 'reasoning_end': Renderer.endReasoning(id, event.round); break;
    case 'answer_start': conversationState.stage = '正在生成回复'; Renderer.beginAnswer(id, event.round); break;
    case 'answer_delta': Renderer.appendAnswer(id, event.round, event.content); break;
    case 'answer_end': Renderer.endAnswer(id, event.round); break;
    case 'usage': Renderer.updateUsage?.(id, event.usage); break;
    case 'tool_call_start':
      conversationState.stage = event.message || `正在执行 ${event.display_name || event.name || '工具'}`;
      conversationState.currentTool = { id: event.id, name: event.display_name || event.name || '工具', startedAt: Date.now(), elapsedMs: 0 };
      Renderer.beginTool(id, event.id, event); renderRunStatus(); break;
    case 'tool_progress':
      Renderer.progressTool(id, event.id, event);
      if (conversationState.currentTool?.id === event.id) {
        conversationState.currentTool.elapsedMs = Number(event.elapsed_ms) || 0;
        conversationState.currentTool.startedAt = Date.now() - conversationState.currentTool.elapsedMs;
        conversationState.stage = event.message || conversationState.stage;
        renderRunStatus();
      }
      break;
    case 'tool_result':
      Renderer.completeTool(id, event.id, event);
      if (conversationState.currentTool?.id === event.id) {
        conversationState.lastToolElapsedMs = Number(event.elapsed_ms) || 0;
        conversationState.currentTool = null; conversationState.stage = event.success ? '正在整理执行结果' : '工具执行失败';
        renderRunStatus();
      }
      break;
    case 'turn_end': Renderer.completeTurn(id, event); finishTurn(id, 'complete'); break;
    case 'cancel_requested':
      if (id === conversationState.activeTurnId) { stop.disabled = true; stop.textContent = '正在停止…'; conversationState.stage = '正在停止'; renderRunStatus(); }
      break;
    case 'cancelled': Renderer.cancelTurn(id, event); finishTurn(id, 'cancelled'); break;
    case 'stream_error': case 'error': Renderer.failTurn(id, event); finishTurn(id, 'failed'); break;
    case 'cleared': Renderer.clearConversation(); finishTurn(null, 'cleared'); showWelcome(); break;
  }
};
window.handleEvent = window.handleMACtrlEvent;
window.handleMACtrlDisconnect = () => {
  conversationState.disconnected = true;
  if (conversationState.activeTurnId) {
    const id = conversationState.activeTurnId; Renderer.disconnectTurn(id); finishTurn(id, 'disconnected');
  } else showConnectionStatus('连接已中断，正在重新连接…', 'disconnected');
};

window.MACtrlSocket.onStateChange(({ state, initError }) => {
  const node = document.querySelector('#server-status');
  if (node) {
    node.dataset.state = state;
    node.textContent = state === 'ready' ? 'MACtrl 服务：已就绪' : state === 'open' ? 'MACtrl 服务：等待后端就绪' : state === 'init_error' ? `MACtrl 服务：初始化失败 ${initError}` : state === 'reconnecting' ? 'MACtrl 服务：重新连接中' : state === 'disconnected' ? 'MACtrl 服务：连接已断开' : 'MACtrl 服务：连接中';
  }
  if (state === 'reconnecting') showConnectionStatus('连接已中断，正在重新连接…', 'disconnected');
  if (state === 'ready' && conversationState.disconnected) {
    conversationState.disconnected = false; showConnectionStatus('连接已恢复', 'complete', 4000);
  }
});
window.MACtrlSocket.connect();
