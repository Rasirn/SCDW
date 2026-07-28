/* 以 turn_id 为边界的渲染器；流式文本和工具状态都原位批量更新。 */
window.Renderer = (() => {
  const box = document.querySelector('#messages');
  const latestButton = document.querySelector('#new-content');
  const turns = new Map();
  const key = value => String(value ?? 0);
  let autoFollow = true;

  const formatDuration = milliseconds => {
    const seconds = Math.max(0, Math.floor((Number(milliseconds) || 0) / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  };
  const relativeTime = milliseconds => {
    const seconds = Math.max(0, Math.floor((Number(milliseconds) || 0) / 1000));
    return seconds < 2 ? '刚刚' : seconds < 60 ? `${seconds} 秒前` : `${Math.floor(seconds / 60)} 分钟前`;
  };
  const nearBottom = () => box.scrollHeight - box.scrollTop - box.clientHeight <= 80;
  const contentChanged = () => {
    if (autoFollow) box.scrollTop = box.scrollHeight;
    else latestButton.hidden = false;
  };
  box.addEventListener('scroll', () => {
    autoFollow = nearBottom();
    if (autoFollow) latestButton.hidden = true;
  }, { passive: true });
  latestButton.addEventListener('click', () => {
    autoFollow = true;
    latestButton.hidden = true;
    box.scrollTop = box.scrollHeight;
  });

  function turn(id) {
    const value = turns.get(id);
    if (!value) throw new Error(`未知回合：${id}`);
    return value;
  }
  function diagnostic(id, message) {
    const node = document.createElement('div');
    node.className = 'message error'; node.textContent = message;
    box.append(node); contentChanged();
    console.warn(`[MACtrl] ${id}: ${message}`);
  }
  function createStreamNode(container) {
    const textNode = document.createTextNode('');
    container.append(textNode);
    return { node: container, textNode, pending: '', scheduled: false };
  }
  function appendBuffered(stream, value) {
    stream.pending += value || '';
    if (stream.scheduled) return;
    stream.scheduled = true;
    requestAnimationFrame(() => {
      stream.scheduled = false;
      if (!stream.pending) return;
      stream.textNode.data += stream.pending;
      stream.pending = '';
      contentChanged();
    });
  }
  function flush(stream) {
    if (!stream?.pending) return;
    stream.textNode.data += stream.pending;
    stream.pending = '';
    contentChanged();
  }
  function safeArgumentSummary(argumentsValue) {
    if (!argumentsValue || typeof argumentsValue !== 'object' || Array.isArray(argumentsValue)) {
      return typeof argumentsValue === 'string' && argumentsValue.length > 240
        ? { summary: `已省略长文本（${argumentsValue.length} 字符）` }
        : argumentsValue;
    }
    const result = {};
    Object.entries(argumentsValue).forEach(([name, value]) => {
      if (typeof value === 'string' && (name.toLowerCase() === 'xml_content' || value.length > 240)) {
        result[name] = { summary: `已省略长文本（${value.length} 字符，约 ${value.split('\n').length} 行）` };
      } else result[name] = value;
    });
    return result;
  }
  function compactResult(value, limit = 900) {
    const normalized = String(value || '').trim();
    if (!normalized) return '工具未返回文本结果。';
    return normalized.length > limit ? `${normalized.slice(0, limit)}\n…（其余内容已省略）` : normalized;
  }
  function setToolTerminal(tool, status, data = {}) {
    if (!tool || tool.status !== 'running') return;
    tool.status = status;
    tool.elapsedMs = Number(data.elapsed_ms ?? (Date.now() - tool.startedAt));
    tool.node.classList.remove('success', 'fail', 'cancelled');
    tool.node.classList.add(status === 'success' ? 'success' : ['failed', 'interrupted'].includes(status) ? 'fail' : 'cancelled');
    tool.icon.className = 'tool-icon';
    tool.icon.textContent = status === 'success' ? '✓' : ['failed', 'interrupted'].includes(status) ? '!' : '■';
    tool.stateNode.textContent = status === 'success' ? '已完成' : status === 'failed' ? '失败' : status === 'interrupted' ? '已中断' : '已取消';
    tool.elapsedNode.textContent = `耗时 ${formatDuration(tool.elapsedMs)}`;
    tool.recentNode.textContent = '刚刚';
    contentChanged();
  }

  return {
    formatDuration,
    hasTurn: id => turns.has(id),
    isAutoFollowing: () => autoFollow,
    beginTurn(id, { userText, mode }) {
      if (turns.has(id)) return turns.get(id);
      const root = document.createElement('article'); root.className = 'conversation-turn'; root.dataset.turnId = id;
      const user = document.createElement('div'); user.className = 'message user'; user.textContent = userText;
      const timeline = document.createElement('div'); timeline.className = 'assistant-timeline'; timeline.dataset.mode = mode;
      root.append(user, timeline); box.append(root);
      const state = { id, root, userNode: user, timelineNode: timeline, reasoningNodes: new Map(), answerNodes: new Map(), toolNodes: new Map(), status: 'running' };
      turns.set(id, state); contentChanged(); return state;
    },
    beginReasoning(id, round) {
      const state = turn(id), k = key(round);
      if (state.reasoningNodes.has(k)) return state.reasoningNodes.get(k);
      const node = document.createElement('details'); node.className = 'thinking'; node.open = true; node.dataset.round = k;
      const summary = document.createElement('summary'); summary.textContent = '正在思考';
      const content = document.createElement('div'); node.append(summary, content); state.timelineNode.append(node);
      const stream = createStreamNode(content); stream.detailsNode = node;
      state.reasoningNodes.set(k, stream); contentChanged(); return stream;
    },
    appendReasoning(id, round, value) { appendBuffered(this.beginReasoning(id, round), value); },
    endReasoning(id, round) {
      const stream = turn(id).reasoningNodes.get(key(round)); if (!stream) return;
      flush(stream); stream.detailsNode.open = false; stream.detailsNode.querySelector('summary').textContent = '思考过程';
    },
    beginAnswer(id, round) {
      const state = turn(id), k = key(round);
      if (state.answerNodes.has(k)) return state.answerNodes.get(k);
      const node = document.createElement('section'); node.className = 'message answer'; node.dataset.round = k;
      state.timelineNode.append(node); const stream = createStreamNode(node);
      state.answerNodes.set(k, stream); contentChanged(); return stream;
    },
    appendAnswer(id, round, value) { appendBuffered(this.beginAnswer(id, round), value); },
    endAnswer(id, round) {
      const stream = turn(id).answerNodes.get(key(round)); if (!stream) return;
      flush(stream); stream.node.dataset.complete = 'true';
    },
    beginTool(id, callId, data) {
      const state = turn(id), k = key(callId);
      if (state.toolNodes.has(k)) return state.toolNodes.get(k);
      const node = document.createElement('details'); node.className = 'tool'; node.dataset.toolCallId = k; node.open = true;
      const head = document.createElement('summary'); head.className = 'tool-head';
      const icon = document.createElement('span'); icon.className = 'tool-spinner'; icon.setAttribute('aria-label', '执行中');
      const title = document.createElement('span'); title.className = 'tool-title'; title.textContent = data.display_name || data.name || '工具';
      const stateNode = document.createElement('span'); stateNode.className = 'tool-state'; stateNode.textContent = '执行中';
      head.append(icon, title, stateNode);
      const activity = document.createElement('div'); activity.className = 'tool-activity'; activity.textContent = data.message || `正在执行：${title.textContent}`;
      const meta = document.createElement('div'); meta.className = 'tool-meta';
      const elapsedNode = document.createElement('span'); elapsedNode.textContent = '已运行 00:00';
      const recentNode = document.createElement('span'); recentNode.textContent = '最近活动：刚刚'; meta.append(elapsedNode, recentNode);
      const args = document.createElement('details'); args.className = 'tool-arguments';
      const argsTitle = document.createElement('summary'); argsTitle.textContent = '查看参数摘要';
      const argsText = document.createElement('pre'); argsText.textContent = JSON.stringify(safeArgumentSummary(data.arguments ?? {}), null, 2);
      args.append(argsTitle, argsText); node.append(head, activity, meta, args); state.timelineNode.append(node);
      const tool = { node, icon, stateNode, activity, elapsedNode, recentNode, startedAt: Date.now(), lastActivityAt: Date.now(), elapsedMs: 0, status: 'running', name: title.textContent };
      state.toolNodes.set(k, tool); contentChanged(); return tool;
    },
    progressTool(id, callId, data) {
      const tool = turns.get(id)?.toolNodes.get(key(callId));
      if (!tool || tool.status !== 'running') return;
      tool.lastActivityAt = Date.now();
      if (Number.isFinite(Number(data.elapsed_ms))) {
        tool.elapsedMs = Number(data.elapsed_ms); tool.startedAt = Date.now() - tool.elapsedMs;
      }
      tool.activity.textContent = data.message || `正在执行：${tool.name}`;
      tool.stateNode.textContent = data.stage === 'waiting_safe_stop' ? '等待安全停止' : '执行中';
      tool.elapsedNode.textContent = `已运行 ${formatDuration(tool.elapsedMs)}`;
      tool.recentNode.textContent = '最近活动：刚刚';
      contentChanged();
    },
    completeTool(id, callId, data) {
      const tool = turns.get(id)?.toolNodes.get(key(callId));
      if (!tool || tool.status !== 'running') return;
      const status = data.success ? 'success' : 'failed'; setToolTerminal(tool, status, data);
      tool.activity.textContent = data.success ? '工具执行成功' : `错误：${compactResult(data.content, 220).split('\n')[0]}`;
      const result = document.createElement('div'); result.className = 'tool-result';
      if (data.success) result.textContent = compactResult(data.content);
      else {
        result.textContent = '可展开查看错误详情。';
        const details = document.createElement('details'); details.className = 'tool-error-details';
        const summary = document.createElement('summary'); summary.textContent = '查看详细错误';
        const pre = document.createElement('pre'); pre.textContent = compactResult(data.content, 4000);
        details.append(summary, pre); result.append(details);
      }
      tool.node.append(result); contentChanged();
    },
    tick(now = Date.now()) {
      turns.forEach(state => state.toolNodes.forEach(tool => {
        if (tool.status !== 'running') return;
        tool.elapsedMs = now - tool.startedAt;
        tool.elapsedNode.textContent = `已运行 ${formatDuration(tool.elapsedMs)}`;
        tool.recentNode.textContent = `最近活动：${relativeTime(now - tool.lastActivityAt)}`;
      }));
    },
    completeTurn(id) {
      const state = turns.get(id); if (!state) return; state.status = 'complete';
      state.reasoningNodes.forEach(stream => { flush(stream); stream.detailsNode.open = false; });
      state.answerNodes.forEach(flush);
    },
    failTurn(id, error) {
      const state = turns.get(id); if (!state) { diagnostic(id, error.message || '未知错误'); return; }
      state.status = 'failed'; state.toolNodes.forEach(tool => setToolTerminal(tool, 'failed', error));
      const node = document.createElement('div'); node.className = 'message error'; node.textContent = `错误：${error.message || '未知错误'}`; state.timelineNode.append(node); contentChanged();
    },
    cancelTurn(id) {
      const state = turns.get(id); if (!state) return; state.status = 'cancelled';
      state.toolNodes.forEach(tool => setToolTerminal(tool, 'cancelled'));
      const node = document.createElement('div'); node.className = 'message notice'; node.textContent = '已停止生成'; state.timelineNode.append(node); contentChanged();
    },
    disconnectTurn(id) {
      const state = turns.get(id); if (!state) return; state.status = 'disconnected';
      state.toolNodes.forEach(tool => setToolTerminal(tool, 'interrupted'));
      const node = document.createElement('div'); node.className = 'message notice'; node.textContent = '连接已中断，本轮可能未完成。'; state.timelineNode.append(node); contentChanged();
    },
    clearConversation() { turns.clear(); box.replaceChildren(); autoFollow = true; latestButton.hidden = true; },
    diagnostic
  };
})();
