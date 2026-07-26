/* 以 turn_id 为边界的会话渲染器：历史回合绝不共享节点。 */
window.Renderer = (() => {
  const box = document.querySelector('#messages');
  const turns = new Map();
  const key = value => String(value ?? 0);
  const text = value => document.createTextNode(value || '');
  function turn(id) { const value = turns.get(id); if (!value) throw new Error(`未知回合：${id}`); return value; }
  function diagnostic(id, message) { const node = document.createElement('div'); node.className = 'message error'; node.textContent = message; box.append(node); console.warn(`[MACtrl] ${id}: ${message}`); }
  return {
    hasTurn: id => turns.has(id),
    beginTurn(id, { userText, mode }) {
      if (turns.has(id)) return turns.get(id);
      const root = document.createElement('article'); root.className = 'conversation-turn'; root.dataset.turnId = id;
      const user = document.createElement('div'); user.className = 'message user'; user.textContent = userText;
      const timeline = document.createElement('div'); timeline.className = 'assistant-timeline'; timeline.dataset.mode = mode;
      root.append(user, timeline); box.append(root);
      const state = { id, root, userNode:user, timelineNode:timeline, reasoningNodes:new Map(), answerNodes:new Map(), toolNodes:new Map(), status:'running' };
      turns.set(id, state); return state;
    },
    beginReasoning(id, round) {
      const state = turn(id), k = key(round); if (state.reasoningNodes.has(k)) return state.reasoningNodes.get(k);
      const node = document.createElement('details'); node.className = 'thinking'; node.open = true; node.dataset.round = k;
      const summary = document.createElement('summary'); summary.textContent = '正在思考'; node.append(summary); state.timelineNode.append(node); state.reasoningNodes.set(k,node); return node;
    },
    appendReasoning(id, round, value) { this.beginReasoning(id,round).append(text(value)); },
    endReasoning(id, round) { const node = turn(id).reasoningNodes.get(key(round)); if (node) { node.open=false; node.querySelector('summary').textContent='思考过程'; } },
    beginAnswer(id, round) {
      const state = turn(id), k = key(round); if (state.answerNodes.has(k)) return state.answerNodes.get(k);
      const node=document.createElement('section'); node.className='message answer'; node.dataset.round=k; state.timelineNode.append(node); state.answerNodes.set(k,node); return node;
    },
    appendAnswer(id, round, value) { this.beginAnswer(id,round).append(text(value)); },
    endAnswer(id, round) { const node=turn(id).answerNodes.get(key(round)); if(node) node.dataset.complete='true'; },
    beginTool(id, callId, data) {
      const state=turn(id), k=key(callId); if(state.toolNodes.has(k)) return state.toolNodes.get(k);
      const node=document.createElement('details'); node.className='tool'; node.dataset.toolCallId=k;
      node.innerHTML='<summary></summary><pre></pre>'; node.querySelector('summary').textContent=`${data.display_name || data.name || '工具'} · 执行中`;
      node.querySelector('pre').textContent=JSON.stringify(data.arguments ?? {},null,2); state.timelineNode.append(node); state.toolNodes.set(k,node); return node;
    },
    completeTool(id, callId, data) {
      const node=turn(id).toolNodes.get(key(callId)); if(!node){ diagnostic(id,`未匹配的工具结果：${callId}`); return; }
      node.classList.add(data.success?'success':'fail'); node.querySelector('summary').textContent += `${data.success?' · 已完成':' · 失败'} · ${data.elapsed_ms ?? '?'}ms`;
      node.querySelector('pre').textContent += `\n\n${data.content || ''}`;
    },
    completeTurn(id) { const state=turns.get(id); if(!state) return; state.status='complete'; state.reasoningNodes.forEach(node=>node.open=false); },
    failTurn(id, error) { const state=turns.get(id); if(!state){diagnostic(id,error.message||'未知错误');return;} state.status='failed'; const node=document.createElement('div');node.className='message error';node.textContent=`错误：${error.message||'未知错误'}`;state.timelineNode.append(node); },
    cancelTurn(id) { const state=turns.get(id); if(!state)return; state.status='cancelled'; const node=document.createElement('div');node.className='message notice';node.textContent='已停止生成';state.timelineNode.append(node); },
    disconnectTurn(id) { const state=turns.get(id); if(!state)return; state.status='disconnected'; const node=document.createElement('div');node.className='message notice';node.textContent='连接已中断';state.timelineNode.append(node); },
    clearConversation() { turns.clear(); box.replaceChildren(); },
    diagnostic
  };
})();
