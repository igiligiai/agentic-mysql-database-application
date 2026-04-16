// ═══════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════
const state = {
  connected: false,
  backendUrl: 'http://localhost:8000',
  sessionId: (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `session-${Date.now()}`,
  schema: [],
  tableList: [],
  lineageData: null,
  demoMode: false,
  isThinking: false,
  lineageSim: null,
  lineageZoom: null,
};

// ═══════════════════════════════════════════════
// BACKEND COMMUNICATION
// ═══════════════════════════════════════════════
async function callBackend(endpoint, body = {}) {
  const url = state.backendUrl + endpoint;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || `HTTP ${res.status}`);
  }
  return res.json();
}

async function connectToBackend() {
  const url = document.getElementById('backend-url').value.trim();

  state.backendUrl = url;
  setStatus('Connecting...', 'warn');

  try {
    const data = await callBackend('/ping');
    state.connected = true;
    state.demoMode = false;
    state.tableList = data.tables || [];
    setConnected(true);
    renderTableList();
    await loadSchema();
    await loadLineage();
    document.getElementById('setup-notice').style.display = 'none';
    setStatus(`Connected · ${state.tableList.length} objects`, 'ok');
  } catch (e) {
    setConnected(false);
    setStatus('Connection failed: ' + e.message, 'err');
    addDemoMode();
  }
}

// ═══════════════════════════════════════════════
// DEMO MODE (when backend not available)
// ═══════════════════════════════════════════════
const DEMO_SCHEMA = [
  { name: 'cars_bronze', type: 'TABLE', layer: 'bronze', columns: [
    { name: 'id', type: 'INT', key: 'PK', nullable: false },
    { name: 'make', type: 'VARCHAR(100)', key: '', nullable: true },
    { name: 'model', type: 'VARCHAR(100)', key: '', nullable: true },
    { name: 'year', type: 'INT', key: '', nullable: true },
    { name: 'price', type: 'DECIMAL(10,2)', key: '', nullable: true },
    { name: 'mileage', type: 'INT', key: '', nullable: true },
    { name: 'fuel_type', type: 'VARCHAR(50)', key: '', nullable: true },
    { name: 'raw_source', type: 'TEXT', key: '', nullable: true },
    { name: 'ingested_at', type: 'TIMESTAMP', key: '', nullable: true },
  ]},
  { name: 'cars_silver', type: 'TABLE', layer: 'silver', columns: [
    { name: 'car_id', type: 'INT', key: 'PK', nullable: false },
    { name: 'make', type: 'VARCHAR(100)', key: 'IDX', nullable: false },
    { name: 'model', type: 'VARCHAR(100)', key: '', nullable: false },
    { name: 'year', type: 'YEAR', key: 'IDX', nullable: false },
    { name: 'price_usd', type: 'DECIMAL(12,2)', key: '', nullable: false },
    { name: 'mileage_km', type: 'INT', key: '', nullable: true },
    { name: 'fuel_type', type: 'ENUM', key: '', nullable: false },
    { name: 'bronze_id', type: 'INT', key: 'FK', nullable: false },
    { name: 'cleaned_at', type: 'TIMESTAMP', key: '', nullable: false },
  ]},
  { name: 'brands', type: 'TABLE', layer: 'silver', columns: [
    { name: 'brand_id', type: 'INT', key: 'PK', nullable: false },
    { name: 'name', type: 'VARCHAR(100)', key: '', nullable: false },
    { name: 'country', type: 'VARCHAR(80)', key: '', nullable: true },
    { name: 'founded_year', type: 'INT', key: '', nullable: true },
  ]},
  { name: 'vw_cars_gold', type: 'VIEW', layer: 'gold', columns: [
    { name: 'car_id', type: 'INT', key: '', nullable: false },
    { name: 'make', type: 'VARCHAR(100)', key: '', nullable: false },
    { name: 'model', type: 'VARCHAR(100)', key: '', nullable: false },
    { name: 'year', type: 'YEAR', key: '', nullable: false },
    { name: 'price_usd', type: 'DECIMAL(12,2)', key: '', nullable: false },
    { name: 'brand_country', type: 'VARCHAR(80)', key: '', nullable: true },
  ]},
  { name: 'vw_price_summary', type: 'VIEW', layer: 'gold', columns: [
    { name: 'make', type: 'VARCHAR(100)', key: '', nullable: false },
    { name: 'avg_price', type: 'DECIMAL', key: '', nullable: true },
    { name: 'min_price', type: 'DECIMAL', key: '', nullable: true },
    { name: 'max_price', type: 'DECIMAL', key: '', nullable: true },
    { name: 'car_count', type: 'BIGINT', key: '', nullable: true },
  ]},
];

const DEMO_LINEAGE = {
  nodes: [
    { id: 'cars_bronze', type: 'TABLE', layer: 'bronze', desc: 'Raw ingested car listings' },
    { id: 'brands', type: 'TABLE', layer: 'silver', desc: 'Brand reference table' },
    { id: 'cars_silver', type: 'TABLE', layer: 'silver', desc: 'Cleaned & typed car data' },
    { id: 'vw_cars_gold', type: 'VIEW', layer: 'gold', desc: 'Joined cars + brand country' },
    { id: 'vw_price_summary', type: 'VIEW', layer: 'gold', desc: 'Aggregated price summary by make' },
  ],
  edges: [
    { source: 'cars_bronze', target: 'cars_silver', label: 'clean + type' },
    { source: 'brands', target: 'vw_cars_gold', label: 'JOIN' },
    { source: 'cars_silver', target: 'vw_cars_gold', label: 'SELECT' },
    { source: 'cars_silver', target: 'vw_price_summary', label: 'GROUP BY' },
  ]
};

function addDemoMode() {
  state.connected = true;
  state.demoMode = true;
  state.schema = DEMO_SCHEMA;
  state.tableList = DEMO_SCHEMA.map(t => ({ name: t.name, type: t.type }));
  state.lineageData = DEMO_LINEAGE;
  setConnected(true);
  renderTableList();
  renderSchemaCards(DEMO_SCHEMA);
  renderLineageGraph(DEMO_LINEAGE);
  document.getElementById('setup-notice').innerHTML =
    '⚡ <strong>Demo mode</strong>: Backend not reachable — showing example car database schema. Queries will use simulated data. <a href="#" onclick="showBackendInstructions()">Setup real backend →</a>';
  setStatus('Demo mode · 5 objects', 'warn');
}

// ═══════════════════════════════════════════════
// TOOL EXECUTION
// ═══════════════════════════════════════════════
async function executeTool(name, args) {
  if (state.demoMode || !state.connected) {
    return executeToolDemo(name, args);
  }
  return callBackend('/tool', { tool: name, args });
}

async function executeToolDemo(name, args) {
  await sleep(400);
  if (name === 'get_schema') return { schema: DEMO_SCHEMA };
  if (name === 'get_table_lineage') return { lineage: DEMO_LINEAGE };
  if (name === 'describe_table') {
    const t = DEMO_SCHEMA.find(s => s.name === args.table_name) || DEMO_SCHEMA[0];
    return { table: t, row_count: Math.floor(Math.random() * 50000 + 1000), last_updated: '2024-03-15 08:42:00' };
  }
  if (name === 'execute_sql') {
    return {
      query: args.query,
      columns: ['make', 'count', 'avg_price'],
      rows: [
        ['Toyota', 1842, 28450.00], ['BMW', 934, 52300.00], ['Ford', 1123, 31200.00],
        ['Honda', 876, 25800.00], ['Mercedes', 645, 68900.00],
      ],
      row_count: 5, duration_ms: 42
    };
  }
  if (name === 'check_data_quality') {
    return {
      table: args.table_name,
      checks: [
        { name: 'Null values', status: 'warn', detail: 'mileage: 3.2% null (312 rows)' },
        { name: 'Duplicates', status: 'ok', detail: 'No duplicates on primary key' },
        { name: 'Price range', status: 'ok', detail: 'All prices > 0, max $890,000 (plausible)' },
        { name: 'Year range', status: 'warn', detail: '14 records with year < 1980' },
        { name: 'Fuel type', status: 'ok', detail: '5 distinct values, all valid' },
      ]
    };
  }
  return { error: 'Unknown tool' };
}

// ═══════════════════════════════════════════════
// AGENT LOOP (Python backend)
// ═══════════════════════════════════════════════
async function runAgent(userMessage) {
  if (state.demoMode) {
    return {
      reply: 'Demo mode is active. Connect to the backend to run the Python agent workflow.',
      tool_calls: []
    };
  }

  const payload = {
    message: userMessage,
    session_id: state.sessionId,
    model: 'gpt-4o',
  };

  return callBackend('/chat', payload);
}

// ═══════════════════════════════════════════════
// SIDE EFFECTS FROM TOOLS
// ═══════════════════════════════════════════════
function handleToolSideEffect(toolName, result) {
  if (toolName === 'get_schema' && result.schema) {
    state.schema = result.schema;
    renderSchemaCards(result.schema);
    renderTableList();
  }
  if (toolName === 'get_table_lineage' && result.lineage) {
    state.lineageData = result.lineage;
    renderLineageGraph(result.lineage);
  }
}

// ═══════════════════════════════════════════════
// CHAT UI
// ═══════════════════════════════════════════════
async function sendMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text || state.isThinking) return;

  input.value = '';
  autoResize(input);
  clearEmptyState();

  addUserMessage(text);
  const thinkingEl = addThinking();
  state.isThinking = true;
  setSendBtn(false);

  try {
    const chatResult = await runAgent(text);

    if (Array.isArray(chatResult.tool_calls)) {
      chatResult.tool_calls.forEach(tc => {
        showToolCall(tc.name, tc.args || {});
        if (tc.result) {
          handleToolSideEffect(tc.name, tc.result);
        }
      });
    }

    thinkingEl.remove();
    addAgentMessage(chatResult.reply || 'No response from agent.');
  } catch (e) {
    thinkingEl.remove();
    addAgentMessage('⚠ Error: ' + e.message);
  }

  state.isThinking = false;
  setSendBtn(true);
}

function clearEmptyState() {
  const empty = document.querySelector('#chat-history .empty');
  if (empty) empty.remove();
}

function addUserMessage(text) {
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `<div class="msg-avatar">U</div><div class="msg-body"><div class="msg-role">YOU</div><div class="msg-content">${escHtml(text)}</div></div>`;
  document.getElementById('chat-history').appendChild(el);
  scrollChat();
}

function addAgentMessage(text) {
  const el = document.createElement('div');
  el.className = 'msg agent';
  el.innerHTML = `<div class="msg-avatar">AB</div><div class="msg-body"><div class="msg-role">AUTOBASE AGENT</div><div class="msg-content">${renderMarkdown(text)}</div></div>`;
  document.getElementById('chat-history').appendChild(el);
  scrollChat();
}

function addThinking() {
  const el = document.createElement('div');
  el.className = 'msg agent';
  el.innerHTML = `<div class="msg-avatar">AB</div><div class="msg-body"><div class="thinking"><div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div><span>thinking...</span></div></div>`;
  document.getElementById('chat-history').appendChild(el);
  scrollChat();
  return el;
}

function showToolCall(name, args) {
  const history = document.getElementById('chat-history');
  const el = document.createElement('div');
  el.className = 'tool-call';
  const argsStr = JSON.stringify(args, null, 2);
  el.innerHTML = `<div class="tool-call-header">⟳ TOOL · ${name}</div><div class="tool-call-body">${escHtml(argsStr)}</div>`;
  history.appendChild(el);

  // If execute_sql, show result table placeholder
  if (name === 'execute_sql' && args.query) {
    const sqlEl = document.createElement('div');
    sqlEl.className = 'sql-block';
    sqlEl.innerHTML = `<div class="sql-block-header"><span class="sql-block-label">SQL</span><button class="copy-btn" onclick="copyToClipboard('${escHtml(args.query)}')">copy</button></div><pre>${escHtml(args.query)}</pre>`;
    history.appendChild(sqlEl);
    // After tool result comes back, render the table
    setTimeout(() => renderLastQueryResult(), 600);
  }
  scrollChat();
}

function renderLastQueryResult() {
  // This gets called after the tool result is in, results shown via addAgentMessage
}

function addResultTable(data) {
  if (!data || !data.columns || !data.rows) return '';
  const cols = data.columns.map(c => `<th>${escHtml(String(c))}</th>`).join('');
  const rows = data.rows.map(r => `<tr>${r.map(v => `<td>${escHtml(String(v ?? 'NULL'))}</td>`).join('')}</tr>`).join('');
  return `<div class="result-wrap"><table class="result-table"><thead><tr>${cols}</tr></thead><tbody>${rows}</tbody></table></div><div class="result-meta">${data.row_count} rows · ${data.duration_ms}ms</div>`;
}

function renderMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^/, '<p>').replace(/$/, '</p>');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function scrollChat() {
  const h = document.getElementById('chat-history');
  h.scrollTop = h.scrollHeight;
}

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function setSendBtn(enabled) {
  document.getElementById('send-btn').disabled = !enabled;
}

function useSuggestion(text) {
  document.getElementById('chat-input').value = text;
  sendMessage();
}

// ═══════════════════════════════════════════════
// SCHEMA PANEL
// ═══════════════════════════════════════════════
async function loadSchema() {
  if (!state.connected) return;
  try {
    const result = await executeTool('get_schema', {});
    if (result.schema) {
      state.schema = result.schema;
      renderSchemaCards(result.schema);
    }
  } catch (e) { setStatus('Schema load failed: ' + e.message, 'err'); }
}

function renderSchemaCards(schema) {
  const container = document.getElementById('schema-content');
  if (!schema || !schema.length) {
    container.innerHTML = '<div class="empty"><div class="empty-icon">⬡</div><div class="empty-text">No tables found</div></div>';
    return;
  }
  container.innerHTML = schema.map(t => {
    const typeClass = t.type === 'VIEW' ? 'view' : '';
    const typeLabel = t.type || 'TABLE';
    const layerColor = t.layer === 'gold' ? 'var(--accent)' : t.layer === 'silver' ? 'var(--accent2)' : 'var(--accent3)';
    const cols = (t.columns || []).map(c => {
      const keyBadge = c.key ? `<span class="col-key ${c.key.toLowerCase()}">${c.key}</span>` : '';
      return `<div class="col-row">
        <div class="col-name">${escHtml(c.name)}</div>
        <div class="col-type">${escHtml(c.type || '')}</div>
        <div>${keyBadge}</div>
        <div class="nullable">${c.nullable ? 'null' : 'not null'}</div>
      </div>`;
    }).join('');
    return `<div class="schema-card" data-table="${escHtml(t.name)}">
      <div class="schema-card-header">
        <div style="width:10px;height:10px;border-radius:2px;background:${layerColor};flex-shrink:0;"></div>
        <div class="schema-card-title">${escHtml(t.name)}</div>
        <span class="table-type ${typeClass}">${typeLabel}</span>
        ${t.layer ? `<span class="table-type" style="margin-left:auto;color:${layerColor};background:transparent;">${t.layer.toUpperCase()}</span>` : ''}
      </div>
      <div class="schema-card-body">
        <div class="col-row header">
          <div>Column</div><div>Type</div><div>Key</div><div>Nullable</div>
        </div>
        ${cols}
      </div>
    </div>`;
  }).join('');
}

function filterSchema(query) {
  document.querySelectorAll('.schema-card').forEach(card => {
    const text = card.textContent.toLowerCase();
    card.style.display = text.includes(query.toLowerCase()) ? '' : 'none';
  });
}

// ═══════════════════════════════════════════════
// TABLE LIST IN SIDEBAR
// ═══════════════════════════════════════════════
function renderTableList() {
  const container = document.getElementById('table-list');
  const tables = state.schema.length ? state.schema : state.tableList;
  if (!tables.length) { container.innerHTML = '<div style="font-size:11px;font-family:var(--mono);color:var(--text3);text-align:center;padding:12px 0;">No tables</div>'; return; }

  // Populate lineage dropdown
  const sel = document.getElementById('lineage-focus');
  sel.innerHTML = '<option value="all">All tables</option>' +
    tables.map(t => `<option value="${escHtml(t.name || t)}">${escHtml(t.name || t)}</option>`).join('');

  container.innerHTML = tables.map(t => {
    const name = t.name || t;
    const type = t.type || 'TABLE';
    const typeClass = type === 'VIEW' ? 'view' : '';
    const layer = t.layer;
    const layerDot = layer ? `<span style="width:6px;height:6px;border-radius:50%;background:${layer==='gold'?'var(--accent)':layer==='silver'?'var(--accent2)':'var(--accent3)'};display:inline-block;flex-shrink:0;"></span>` : '';
    return `<div class="table-item" onclick="selectTable('${escHtml(name)}')" title="${escHtml(name)}">
      ${layerDot}
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(name)}</span>
      <span class="table-type ${typeClass}">${type}</span>
    </div>`;
  }).join('');
}

function selectTable(name) {
  document.querySelectorAll('.table-item').forEach(el => el.classList.remove('active'));
  event.currentTarget.classList.add('active');
  switchTab('schema');
  setTimeout(() => {
    const card = document.querySelector(`.schema-card[data-table="${name}"]`);
    if (card) { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  }, 100);
}

// ═══════════════════════════════════════════════
// LINEAGE GRAPH (D3 Force)
// ═══════════════════════════════════════════════
async function loadLineage() {
  try {
    const result = await executeTool('get_table_lineage', { table_name: 'all' });
    if (result.lineage) {
      state.lineageData = result.lineage;
      renderLineageGraph(result.lineage);
    }
  } catch (e) {
    state.lineageData = DEMO_LINEAGE;
    renderLineageGraph(DEMO_LINEAGE);
  }
}

let lineageSvg, lineageZoom;

function renderLineageGraph(data, focusNode = null) {
  const container = document.getElementById('lineage-svg');
  const W = container.clientWidth || 900;
  const H = container.clientHeight || 600;

  d3.select('#lineage-svg').selectAll('*').remove();

  const svg = d3.select('#lineage-svg');
  lineageZoom = d3.zoom().scaleExtent([0.2, 3]).on('zoom', (e) => g.attr('transform', e.transform));
  svg.call(lineageZoom);

  const g = svg.append('g');

  // Arrow markers
  const defs = svg.append('defs');
  ['#e8ff59', '#59ffe8', '#ff6b35', '#4a4a56'].forEach((color, i) => {
    defs.append('marker').attr('id', `arrow-${i}`)
      .attr('viewBox', '0 0 10 10').attr('refX', 20).attr('refY', 5)
      .attr('markerWidth', 8).attr('markerHeight', 8).attr('orient', 'auto-start-reverse')
      .append('path').attr('d', 'M2 1L8 5L2 9').attr('fill', 'none')
      .attr('stroke', color).attr('stroke-width', 1.5).attr('stroke-linecap', 'round');
  });

  const nodes = data.nodes.map(n => ({ ...n }));
  const edges = data.edges.map(e => ({ ...e }));

  // Force simulation
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(180).strength(0.8))
    .force('charge', d3.forceManyBody().strength(-500))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('x', d3.forceX().x(d => {
      if (d.layer === 'bronze') return W * 0.2;
      if (d.layer === 'silver') return W * 0.5;
      if (d.layer === 'gold') return W * 0.8;
      return W / 2;
    }).strength(0.4))
    .force('collision', d3.forceCollide(80));

  // Layer bands
  const bands = [
    { x: W * 0.05, label: 'BRONZE', color: '#ff6b35' },
    { x: W * 0.35, label: 'SILVER', color: '#59ffe8' },
    { x: W * 0.65, label: 'GOLD', color: '#e8ff59' },
  ];
  bands.forEach(b => {
    g.append('line').attr('x1', b.x + W * 0.15).attr('y1', 20).attr('x2', b.x + W * 0.15).attr('y2', H - 20)
      .attr('stroke', b.color).attr('stroke-width', 0.5).attr('stroke-opacity', 0.12).attr('stroke-dasharray', '4 4');
    g.append('text').attr('x', b.x + W * 0.15).attr('y', 18).attr('text-anchor', 'middle')
      .attr('fill', b.color).attr('font-size', 9).attr('font-family', 'DM Mono, monospace')
      .attr('letter-spacing', 2).attr('opacity', 0.5).text(b.label);
  });

  // Edges
  const link = g.append('g').selectAll('path').data(edges).enter().append('path')
    .attr('fill', 'none').attr('stroke', '#4a4a56').attr('stroke-width', 1.5)
    .attr('stroke-dasharray', d => d.label === 'JOIN' ? '5 3' : 'none')
    .attr('marker-end', 'url(#arrow-3)');

  const linkLabel = g.append('g').selectAll('text').data(edges).enter().append('text')
    .attr('fill', '#4a4a56').attr('font-size', 9).attr('font-family', 'DM Mono, monospace')
    .attr('text-anchor', 'middle').text(d => d.label);

  // Nodes
  const node = g.append('g').selectAll('g').data(nodes).enter().append('g')
    .attr('cursor', 'pointer')
    .call(d3.drag().on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }))
    .on('click', (e, d) => showNodeDetails(d))
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip);

  const getColor = d => {
    if (focusNode && d.id === focusNode) return '#ff6b35';
    if (d.layer === 'gold') return '#e8ff59';
    if (d.layer === 'silver') return '#59ffe8';
    return '#ff6b35';
  };

  const rect = node.append('rect').attr('width', 140).attr('height', 44).attr('x', -70).attr('y', -22)
    .attr('rx', 8).attr('fill', d => getColor(d) + '14')
    .attr('stroke', d => getColor(d)).attr('stroke-width', 1);

  node.append('text').attr('y', -5).attr('text-anchor', 'middle')
    .attr('fill', d => getColor(d)).attr('font-size', 12).attr('font-weight', 600)
    .attr('font-family', 'DM Mono, monospace').text(d => d.id);

  node.append('text').attr('y', 10).attr('text-anchor', 'middle')
    .attr('fill', '#4a4a56').attr('font-size', 9).attr('font-family', 'DM Mono, monospace')
    .text(d => d.type || 'TABLE');

  sim.on('tick', () => {
    link.attr('d', d => {
      const dx = d.target.x - d.source.x;
      const dy = d.target.y - d.source.y;
      const dr = Math.sqrt(dx * dx + dy * dy) * 1.5;
      return `M${d.source.x},${d.source.y}A${dr},${dr} 0 0,1 ${d.target.x},${d.target.y}`;
    });
    linkLabel.attr('x', d => (d.source.x + d.target.x) / 2)
      .attr('y', d => (d.source.y + d.target.y) / 2 - 8);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  document.getElementById('lineage-legend').style.display = 'flex';
}

function lineageZoom(factor) {
  if (!lineageZoom) return;
  d3.select('#lineage-svg').transition().duration(300).call(lineageZoom.scaleBy, factor);
}

function renderLineage(focus) {
  const data = state.lineageData || DEMO_LINEAGE;
  if (focus === 'all') { renderLineageGraph(data); return; }
  // Filter to only nodes connected to focus
  const connected = new Set([focus]);
  data.edges.forEach(e => {
    if (e.source === focus || e.source.id === focus) connected.add(e.target.id || e.target);
    if (e.target === focus || e.target.id === focus) connected.add(e.source.id || e.source);
  });
  const filtered = {
    nodes: data.nodes.filter(n => connected.has(n.id)),
    edges: data.edges.filter(e => connected.has(e.source.id || e.source) && connected.has(e.target.id || e.target))
  };
  renderLineageGraph(filtered, focus);
}

function showNodeDetails(d) {
  const question = `Describe the ${d.type || 'table'} "${d.id}" — what data does it contain, its columns, and its role in the pipeline?`;
  switchTab('chat');
  clearEmptyState();
  document.getElementById('chat-input').value = question;
  sendMessage();
}

// ═══════════════════════════════════════════════
// TOOLTIP
// ═══════════════════════════════════════════════
function showTooltip(e, d) {
  const t = document.getElementById('tooltip');
  t.innerHTML = `<strong>${d.id}</strong><br>${d.type || 'TABLE'} · ${(d.layer || '').toUpperCase()}<br><span style="color:var(--text3)">${d.desc || ''}</span>`;
  t.style.opacity = '1';
  t.style.left = (e.clientX + 12) + 'px';
  t.style.top = (e.clientY - 10) + 'px';
  document.addEventListener('mousemove', moveTooltip);
}
function moveTooltip(e) {
  const t = document.getElementById('tooltip');
  t.style.left = (e.clientX + 12) + 'px';
  t.style.top = (e.clientY - 10) + 'px';
}
function hideTooltip() {
  document.getElementById('tooltip').style.opacity = '0';
  document.removeEventListener('mousemove', moveTooltip);
}

// ═══════════════════════════════════════════════
// TABS
// ═══════════════════════════════════════════════
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelector(`.tab[onclick*="${name}"]`)?.classList.add('active');
  document.getElementById(`panel-${name}`)?.classList.add('active');

  if (name === 'lineage') {
    setTimeout(() => { if (state.connected) renderLineage('all'); }, 100);
  }
}

// ═══════════════════════════════════════════════
// QUICK ACTIONS
// ═══════════════════════════════════════════════
function quickAction(type) {
  switchTab('chat');
  clearEmptyState();
  const msgs = {
    schema: 'Give me a complete description of all tables and views in this database, including their purpose and key columns.',
    lineage: 'Show me the full data lineage from bronze to gold. Which tables depend on which, and what transformations happen at each step?',
    quality: 'Run a data quality check on the main cars table — check for nulls, duplicates, and any data anomalies.'
  };
  document.getElementById('chat-input').value = msgs[type] || '';
  sendMessage();
}

// ═══════════════════════════════════════════════
// STATUS & CONNECTION UI
// ═══════════════════════════════════════════════
function setConnected(ok) {
  const dot = document.getElementById('conn-dot');
  const label = document.getElementById('conn-label');
  dot.className = 'conn-dot' + (ok ? ' ok' : ' err');
  label.textContent = ok ? 'Connected' : 'Disconnected';
}

function setStatus(msg, level = '') {
  const bar = document.getElementById('status-bar');
  const cls = level === 'ok' ? 'status-ok' : level === 'err' ? 'status-err' : level === 'warn' ? 'status-warn' : '';
  bar.innerHTML = `<div class="status-item ${cls}">${escHtml(msg)}</div>`;
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).catch(() => {});
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function showBackendInstructions() {
  switchTab('chat');
  clearEmptyState();
  addAgentMessage(`## Backend Setup

Run the Python API from the project root:

\`\`\`bash
pip install -e .
python main.py
\`\`\`

The API starts on http://localhost:8000 and serves:

- POST /ping
- POST /tool
- POST /chat

Set OPENAI_API_KEY on the backend for server-side OpenAI initialization.
The frontend does not send API keys.`);
}

// ═══════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════
window.addEventListener('load', () => {
  // Start in demo mode automatically
  addDemoMode();
});
