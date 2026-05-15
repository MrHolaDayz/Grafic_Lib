(function() {
  const API_BASE = 'http://localhost:8000';

  let cardsData = [];
  let arrowsData = [];
  let cardMap = new Map();
  let nodePositions = new Map();
  let expandedCardId = null;
  let activeFieldFilter = 'all';
  let searchQuery = '';
  let searchResultIds = null;
  let searchHideOthers = false;

  // ── Multi-topic ──
  let currentTopicSlug = null;
  let topicsRegistry   = [];

  let renderTimeout = null;
  let viewportBounds = { x:0, y:0, width:0, height:0 };
  const RENDER_MARGIN = 600;
  const WORLD = 10000;

  let panX=0, panY=0, zoom=0.25;
  let isPanning=false, panStart={x:0,y:0}, panStartOffset={x:0,y:0};

  let cardElementsCache = new Map();
  let visibleCardsCache = new Set();

  const appContainer  = document.getElementById('appContainer');
  const loadingEl     = document.getElementById('loading');
  const graphWrapper  = document.getElementById('graphWrapper');
  const cardsLayer    = document.getElementById('cardsLayer');
  const arrowsSvg     = document.getElementById('arrowsSvg');
  const markersDefs   = document.getElementById('markersDefs');
  const arrowsCanvas  = document.getElementById('arrowsCanvas');
  const tooltipEl     = document.getElementById('cardTooltip');
  const searchInput   = document.getElementById('searchInput');
  const searchClear   = document.getElementById('searchClear');
  const searchResults = document.getElementById('searchResults');

  let ctx = arrowsCanvas ? arrowsCanvas.getContext('2d') : null;

  // ===================== HTML HELPERS =====================
  const _decodeEl = document.createElement('textarea');
  function decodeHTML(str) {
    if (!str) return '';
    _decodeEl.innerHTML = String(str).substring(0, 500);
    return _decodeEl.value;
  }
  function safeText(str) {
    const d = decodeHTML(str);
    return d.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  function safeAttr(str) {
    return safeText(str).replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  // ===================== TOPIC SWITCHER =====================
  const topicCurrentBtn  = document.getElementById('topicCurrentBtn');
  const topicCurrentName = document.getElementById('topicCurrentName');
  const topicDropdown    = document.getElementById('topicDropdown');
  const topicList        = document.getElementById('topicList');

  topicCurrentBtn && topicCurrentBtn.addEventListener('click', e => {
    e.stopPropagation();
    topicDropdown.classList.toggle('open');
    topicCurrentBtn.classList.toggle('open');
  });
  document.addEventListener('click', () => {
    topicDropdown && topicDropdown.classList.remove('open');
    topicCurrentBtn && topicCurrentBtn.classList.remove('open');
  });

  async function loadTopics() {
    try {
      const res = await fetch(`${API_BASE}/api/topics`);
      topicsRegistry = await res.json();
    } catch(e) { topicsRegistry = []; }

    if (!topicList) return;
    topicList.innerHTML = '';
    if (!topicsRegistry.length) {
      topicList.innerHTML = '<div style="padding:.6rem .85rem;font-size:.7rem;color:#3a5068;">нет доступных тем</div>';
      return;
    }
    topicsRegistry.forEach(t => {
      const item = document.createElement('div');
      item.className = 'topic-item' + (t.slug === currentTopicSlug ? ' active' : '');
      item.dataset.slug = t.slug;
      item.innerHTML = `
        <span class="topic-item-name">${safeText(t.name)}</span>
        <span class="topic-item-count">${t.cards} карт.</span>`;
      item.addEventListener('click', e => {
        e.stopPropagation();
        topicDropdown.classList.remove('open');
        topicCurrentBtn.classList.remove('open');
        switchTopic(t.slug, t.name);
      });
      topicList.appendChild(item);
    });
  }

  function switchTopic(slug, name) {
    if (slug === currentTopicSlug) return;
    currentTopicSlug = slug;
    if (topicCurrentName) topicCurrentName.textContent = name;
    topicList && topicList.querySelectorAll('.topic-item').forEach(el => {
      el.classList.toggle('active', el.dataset.slug === slug);
    });
    expandedCardId = null;
    clearSearch();
    cardElementsCache.forEach(el => el.remove());
    cardElementsCache.clear();
    visibleCardsCache.clear();
    panX=0; panY=0;
    loadData();
  }

  // ===================== DATA LOAD =====================
  async function loadData() {
    if (!currentTopicSlug) return;
    loadingEl.style.display = 'flex';
    appContainer.style.display = 'none';
    try {
      const [cardsRes, arrowsRes] = await Promise.all([
        fetch(`${API_BASE}/api/${currentTopicSlug}/cards`),
        fetch(`${API_BASE}/api/${currentTopicSlug}/arrows`)
      ]);
      if (!cardsRes.ok) throw new Error('Failed to load cards');
      cardsData  = await cardsRes.json();
      arrowsData = await arrowsRes.json();

      cardMap.clear(); nodePositions.clear();
      cardsData.forEach(card => {
        cardMap.set(card.id, card);
        nodePositions.set(card.id, {
          x: (card.cords?.x || 50) / 100 * WORLD,
          y: (card.cords?.y || 50) / 100 * WORLD
        });
      });
      arrowsData.forEach(a => {
        a._x1=(a.x1/100)*WORLD; a._y1=(a.y1/100)*WORLD;
        a._x2=(a.x2/100)*WORLD; a._y2=(a.y2/100)*WORLD;
        a._color=a.color||'#5e8ab4';
      });

      calculateOptimalZoom();
      loadingEl.style.display = 'none';
      appContainer.style.display = 'flex';
      refreshAll();
    } catch(error) {
      console.error('Error:', error);
      loadingEl.innerHTML = `
        <div style="text-align:center;padding:2rem;">
          <p style="color:#c07a5a;">ошибка загрузки данных</p>
          <p style="font-size:.8rem;color:#60758b;">${error.message}</p>
          <button class="btn" onclick="location.reload()" style="margin-top:1rem;">повторить</button>
        </div>`;
    }
  }

  async function initApp() {
    await loadTopics();
    if (topicsRegistry.length) {
      currentTopicSlug = topicsRegistry[0].slug;
      if (topicCurrentName) topicCurrentName.textContent = topicsRegistry[0].name;
      topicList && topicList.querySelectorAll('.topic-item').forEach((el,i) => {
        if (i===0) el.classList.add('active');
      });
      await loadData();
    } else {
      loadingEl.innerHTML = `
        <div style="text-align:center;padding:2rem;">
          <p style="color:#c07a5a;">нет доступных тем</p>
          <p style="font-size:.8rem;color:#60758b;margin-top:.5rem;">
            Создайте тему:<br>
            <code style="color:#5e8ab4">python create_topic.py</code>
          </p>
        </div>`;
    }
  }

  function calculateOptimalZoom() {
    const count = cardsData.length;
    if (count > 500) zoom = 0.18;
    else if (count > 200) zoom = 0.35;
    else if (count > 50)  zoom = 0.6;
    else zoom = 1;
  }

  // ===================== VIEWPORT =====================
  function updateViewportBounds() {
    if (!graphWrapper) return;
    const rect = graphWrapper.getBoundingClientRect();
    viewportBounds = {
      x:      -panX / zoom - RENDER_MARGIN,
      y:      -panY / zoom - RENDER_MARGIN,
      width:   rect.width  / zoom + RENDER_MARGIN * 2,
      height:  rect.height / zoom + RENDER_MARGIN * 2,
      screenW: rect.width,
      screenH: rect.height
    };
  }

  function isPointVisible(wx, wy) {
    return wx >= viewportBounds.x && wx <= viewportBounds.x + viewportBounds.width &&
           wy >= viewportBounds.y && wy <= viewportBounds.y + viewportBounds.height;
  }

  function isCardVisible(cardId) {
    const pos = nodePositions.get(cardId);
    if (!pos) return false;
    return isPointVisible(pos.x, pos.y);
  }

  function isArrowVisible(a) {
    return isPointVisible(a._x1, a._y1) || isPointVisible(a._x2, a._y2);
  }

  // World → screen
  function wx2s(wx) { return wx * zoom + panX; }
  function wy2s(wy) { return wy * zoom + panY; }

  // ===================== MAIN RENDER LOOP =====================
  function refreshAll() {
    if (renderTimeout) cancelAnimationFrame(renderTimeout);
    renderTimeout = requestAnimationFrame(() => {
      updateViewportBounds();
      renderCards();
      renderArrows();
      renderTimeout = null;
    });
  }

  // ===================== CANVAS ARROWS =====================
  function renderArrows() {
    if (!ctx || !arrowsCanvas) return;

    const sw = viewportBounds.screenW || graphWrapper.clientWidth;
    const sh = viewportBounds.screenH || graphWrapper.clientHeight;

    // Resize canvas only if needed
    if (arrowsCanvas.width !== sw || arrowsCanvas.height !== sh) {
      arrowsCanvas.width  = sw;
      arrowsCanvas.height = sh;
    }

    ctx.clearRect(0, 0, sw, sh);

    // If expanded — draw only related arrows via SVG (handled below), canvas draws rest dimmed
    if (expandedCardId) {
      renderSvgArrows();  // interactive arrows via SVG
      return;
    }

    // Clear SVG interactive arrows when not expanded
    clearSvgArrows();

    // Фильтрация по типу связи временно отключена — будет переработана
    const arrowsToDraw = arrowsData;

    const MAX = 6000;
    let count = 0;

    // Group by color to minimise ctx state changes
    const byColor = new Map();
    for (const a of arrowsToDraw) {
      if (count >= MAX) break;
      if (!isArrowVisible(a)) continue;
      if (!byColor.has(a._color)) byColor.set(a._color, []);
      byColor.get(a._color).push(a);
      count++;
    }

    const ARROW_LEN = 7 * zoom;
    const ARROW_W   = 3 * zoom;

    for (const [color, arrows] of byColor) {
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth   = Math.max(0.5, 1.2 * zoom);

      for (const a of arrows) {
        const sx = wx2s(a._x1), sy = wy2s(a._y1);
        const ex = wx2s(a._x2), ey = wy2s(a._y2);
        ctx.moveTo(sx, sy);
        ctx.lineTo(ex, ey);
      }
      ctx.stroke();

      // Draw arrowheads
      ctx.globalAlpha = 0.7;
      ctx.fillStyle = color;
      for (const a of arrows) {
        drawArrowHead(ctx, wx2s(a._x1), wy2s(a._y1), wx2s(a._x2), wy2s(a._y2), ARROW_LEN, ARROW_W);
      }
    }

    ctx.globalAlpha = 1;
  }

  function drawArrowHead(ctx, x1, y1, x2, y2, len, width) {
    const dx = x2 - x1, dy = y2 - y1;
    const dist = Math.sqrt(dx*dx + dy*dy);
    if (dist < 1) return;
    const ux = dx/dist, uy = dy/dist;
    const px = -uy, py = ux;
    const tip = { x: x2, y: y2 };
    const base = { x: x2 - ux*len, y: y2 - uy*len };
    ctx.beginPath();
    ctx.moveTo(tip.x, tip.y);
    ctx.lineTo(base.x + px*width, base.y + py*width);
    ctx.lineTo(base.x - px*width, base.y - py*width);
    ctx.closePath();
    ctx.fill();
  }

  // ===================== SVG ARROWS (expanded mode) =====================
  function clearSvgArrows() {
    // Remove all lines, keep defs
    Array.from(arrowsSvg.children).forEach(c => {
      if (c.tagName !== 'defs') c.remove();
    });
  }

  function renderSvgArrows() {
    clearSvgArrows();
    if (!expandedCardId) return;

    // Also clear canvas in expanded mode
    if (ctx) {
      ctx.clearRect(0, 0, arrowsCanvas.width || 0, arrowsCanvas.height || 0);
    }

    const related = arrowsData.filter(a =>
      a.source_id === expandedCardId || a.target_id === expandedCardId
    );

    // Ensure markers
    updateMarkers(related);

    // Use foreignObject trick: SVG is in viewport, coordinates are world units
    for (const a of related) {
      if (!isArrowVisible(a)) continue;

      const color = a._color;
      const markerId = `m-${color.replace('#','')}`;

      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", a._x1);
      line.setAttribute("y1", a._y1);
      line.setAttribute("x2", a._x2);
      line.setAttribute("y2", a._y2);
      line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", 2.5 / zoom);  // compensate viewport scale
      line.setAttribute("marker-end", `url(#${markerId})`);
      line.setAttribute("opacity", "0.9");
      line.dataset.source = a.source_id;
      line.dataset.target = a.target_id;
      line.style.pointerEvents = 'visibleStroke';
      line.style.cursor = 'pointer';

      line.addEventListener('click', e => {
        e.stopPropagation();
        const tid = parseInt(line.dataset.target);
        navigateToCard(tid === expandedCardId ? parseInt(line.dataset.source) : tid);
      });
      line.addEventListener('mouseenter', () => {
        line.setAttribute('stroke-width', 4 / zoom);
        line.setAttribute('opacity','1');
      });
      line.addEventListener('mouseleave', () => {
        line.setAttribute('stroke-width', 2.5 / zoom);
        line.setAttribute('opacity','0.9');
      });

      arrowsSvg.appendChild(line);
    }
  }

  function updateMarkers(arrows) {
    markersDefs.innerHTML = '';
    const colors = new Set(arrows.map(a => a._color));
    colors.forEach(color => {
      const id = `m-${color.replace('#','')}`;
      const marker = document.createElementNS("http://www.w3.org/2000/svg","marker");
      marker.setAttribute("id", id);
      marker.setAttribute("markerWidth", "8");
      marker.setAttribute("markerHeight", "6");
      marker.setAttribute("refX", "7");
      marker.setAttribute("refY", "3");
      marker.setAttribute("orient", "auto");
      marker.setAttribute("markerUnits", "userSpaceOnUse");
      const sz = 8 / zoom;
      marker.setAttribute("markerWidth",  sz);
      marker.setAttribute("markerHeight", sz * 0.75);
      marker.setAttribute("refX", sz * 0.875);
      marker.setAttribute("refY", sz * 0.375);
      const poly = document.createElementNS("http://www.w3.org/2000/svg","polygon");
      poly.setAttribute("points", `0 0, ${sz} ${sz*0.375}, 0 ${sz*0.75}`);
      poly.setAttribute("fill", color);
      marker.appendChild(poly);
      markersDefs.appendChild(marker);
    });
  }

  // ===================== CARDS =====================
  function renderCards() {
    const activeIds = searchResultIds;

    const newVisible = new Set();
    for (const card of cardsData) {
      const cid = card.id;
      if (cid === expandedCardId || isCardVisible(cid)) {
        // Hide non-matching cards only when filter is active AND hideOthers is on
        if (activeIds && searchHideOthers && cid !== expandedCardId && !activeIds.has(cid)) continue;
        newVisible.add(cid);
      }
    }

    // Remove off-screen
    for (const cid of visibleCardsCache) {
      if (!newVisible.has(cid)) {
        const el = cardElementsCache.get(cid);
        if (el) { el.remove(); cardElementsCache.delete(cid); }
      }
    }

    // Add / update
    for (const cid of newVisible) {
      if (!visibleCardsCache.has(cid)) createCardElement(cid);
      updateCardElement(cid);
    }

    visibleCardsCache = newVisible;
  }

  function createCardElement(cardId) {
    const card = cardMap.get(cardId);
    if (!card || !cardsLayer) return;
    const el = document.createElement('article');
    el.className = 'card';
    el.dataset.id = cardId;
    el.innerHTML = getCompactHTML(card);
    el.addEventListener('click', e => handleCardClick(e, cardId));
    cardsLayer.appendChild(el);
    cardElementsCache.set(cardId, el);
  }

  function updateCardElement(cardId) {
    const el = cardElementsCache.get(cardId);
    if (!el) return;
    const card = cardMap.get(cardId);
    if (!card) return;
    const pos = nodePositions.get(cardId);
    if (!pos) return;

    el.style.left = pos.x + 'px';
    el.style.top  = pos.y + 'px';

    const sectionColor = card.section_color || '#5e8ab4';
    const isExpanded  = expandedCardId === cardId;
    const isConnected = expandedCardId && getCardLinks(expandedCardId).includes(cardId);
    const isSearch    = searchResultIds && searchResultIds.has(cardId);
    const isDimmed    = searchResultIds && !searchHideOthers && !isSearch && !isExpanded && !isConnected;

    el.style.borderColor = sectionColor;
    el.style.borderWidth = isExpanded ? '2px' : '1.5px';

    const r = parseInt(sectionColor.slice(1,3),16);
    const g = parseInt(sectionColor.slice(3,5),16);
    const b = parseInt(sectionColor.slice(5,7),16);
    const bgR = Math.floor(r*.15), bgG = Math.floor(g*.15), bgB = Math.floor(b*.15);
    const d2R = Math.floor(r*.07), d2G = Math.floor(g*.07), d2B = Math.floor(b*.07);
    el.style.background = `linear-gradient(150deg,rgb(${bgR},${bgG},${bgB}),rgb(${d2R},${d2G},${d2B}))`;

    if (isExpanded) {
      el.classList.add('expanded');
      el.classList.remove('connected', 'search-match');
      el.style.boxShadow = `0 0 28px ${sectionColor}70,0 0 56px ${sectionColor}25,0 8px 24px rgba(0,0,0,.6)`;
      el.style.zIndex = '25';
      if (!el.querySelector('.expanded-header')) el.innerHTML = getExpandedHTML(card);
    } else {
      el.classList.remove('expanded');
      if (isSearch) el.classList.add('search-match'); else el.classList.remove('search-match');
      el.style.opacity = isDimmed ? '0.25' : '';
      if (isConnected) {
        el.classList.add('connected');
        el.style.boxShadow = `0 0 14px ${sectionColor}50,0 4px 12px rgba(0,0,0,.5)`;
        el.style.zIndex = '15';
      } else {
        el.classList.remove('connected');
        el.style.boxShadow = '0 2px 8px rgba(0,0,0,.5)';
        el.style.zIndex = '';
      }
      if (el.querySelector('.expanded-header')) el.innerHTML = getCompactHTML(card);
    }
  }

  // ---- Card HTML ----
  function getCompactHTML(card) {
    const name = safeText(card.name);
    return `
      <div class="card-name-main" title="${safeAttr(card.name)}">${name}</div>
      <div class="card-id-sub">#${card.id}</div>
    `;
  }

  function getExpandedHTML(card) {
    const links = getCardLinksByField(card.id);
    let linksHTML = '';
    for (const [field, ids] of Object.entries(links)) {
      if (!ids || ids.length === 0) continue;
      const label = field === 'teorems' ? 'теоремы' : field === 'usein' ? 'usein' : 'раздел';
      linksHTML += `<div class="link-group-compact">
        <span class="link-label-compact">${label}:</span>
        <span class="link-tags-row">
          ${ids.slice(0,8).map(id => {
            const c = cardMap.get(id);
            const name = c ? safeAttr(c.name) : '';
            return `<span class="link-tag-compact" data-link-id="${id}" data-link-name="${name}">#${id}</span>`;
          }).join('')}
          ${ids.length > 8 ? `<span class="link-more">+${ids.length-8}</span>` : ''}
        </span>
      </div>`;
    }
    return `
      <div class="expanded-header">
        <span class="card-id-compact">#${card.id}</span>
        <button class="collapse-btn-compact" title="свернуть">✕</button>
      </div>
      <div class="card-name-expanded">${safeText(card.name)}</div>
      <div class="card-preview-compact">${safeText(card.preview || '')}</div>
      <div class="card-links-compact">${linksHTML || '<span class="no-links">нет связей</span>'}</div>
      <div class="card-hint-compact">нажмите для статьи ↗</div>
    `;
  }

  // ===================== HELPERS =====================
  function getCardLinks(cardId) {
    const card = cardMap.get(cardId);
    if (!card) return [];
    return [...new Set([...(card.teorems||[]),...(card.usein||[]),...(card.chapter||[])])].slice(0,40);
  }

  function getCardLinksByField(cardId) {
    const card = cardMap.get(cardId);
    if (!card) return { teorems:[], usein:[], chapter:[] };
    return {
      teorems: (card.teorems||[]).slice(0,10),
      usein:   (card.usein  ||[]).slice(0,10),
      chapter: (card.chapter||[]).slice(0,10)
    };
  }

  // ===================== TOOLTIP =====================
  function showTooltip(text, e) {
    tooltipEl.textContent = text;
    tooltipEl.classList.add('visible');
    positionTooltip(e);
  }
  function hideTooltip() {
    tooltipEl.classList.remove('visible');
  }
  function positionTooltip(e) {
    const x = e.clientX + 12;
    const y = e.clientY - 8;
    tooltipEl.style.left = Math.min(x, window.innerWidth - tooltipEl.offsetWidth - 8) + 'px';
    tooltipEl.style.top  = Math.max(y, 4) + 'px';
  }

  // Delegate tooltip on link tags
  document.addEventListener('mouseover', e => {
    const tag = e.target.closest('.link-tag-compact');
    if (tag && tag.dataset.linkName) {
      showTooltip(decodeHTML(tag.dataset.linkName), e);
    }
  });
  document.addEventListener('mouseout', e => {
    if (e.target.closest('.link-tag-compact')) hideTooltip();
  });
  document.addEventListener('mousemove', e => {
    if (e.target.closest('.link-tag-compact')) positionTooltip(e);
  });

  // ===================== SEARCH =====================
  let searchDebounce = null;

  searchInput && searchInput.addEventListener('input', () => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(applySearch, 200);
  });

  searchInput && searchInput.addEventListener('keydown', e => {
    if (e.key === 'Escape') { clearSearch(); searchInput.blur(); }
    if (e.key === 'Enter') {
      const first = searchResults.querySelector('.search-result-item');
      if (first) first.click();
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const items = searchResults.querySelectorAll('.search-result-item');
      if (items.length) items[0].focus();
    }
  });

  searchResults && searchResults.addEventListener('keydown', e => {
    const items = Array.from(searchResults.querySelectorAll('.search-result-item'));
    const idx = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' && idx < items.length-1) { e.preventDefault(); items[idx+1].focus(); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); idx > 0 ? items[idx-1].focus() : searchInput.focus(); }
    if (e.key === 'Escape')    { clearSearch(); searchInput.blur(); }
  });

  searchClear && searchClear.addEventListener('click', () => { clearSearch(); searchInput.focus(); });

  // Hide-others toggle
  document.addEventListener('change', e => {
    if (e.target.id === 'searchHideChk') {
      searchHideOthers = e.target.checked;
      refreshAll();
    }
  });

  function applySearch() {
    const q = (searchInput.value || '').trim().toLowerCase();
    searchQuery = q;

    if (!q) { clearSearch(); return; }

    searchClear.style.display = 'flex';

    const matched = cardsData.filter(c =>
      decodeHTML(c.name    || '').toLowerCase().includes(q) ||
      decodeHTML(c.preview || '').toLowerCase().includes(q)
    );

    searchResultIds = new Set(matched.map(c => c.id));

    // Update count badge
    const badge = document.getElementById('searchCountBadge');
    if (badge) badge.textContent = matched.length;
    const hideWrap = document.getElementById('searchHideWrap');
    if (hideWrap) hideWrap.style.display = 'flex';

    renderSearchDropdown(matched.slice(0, 20));
    refreshAll();
  }

  function renderSearchDropdown(matches) {
    if (!matches.length) {
      searchResults.innerHTML = '<div class="search-empty">ничего не найдено</div>';
      searchResults.classList.add('open');
      return;
    }
    searchResults.innerHTML = matches.map(c => `
      <div class="search-result-item" tabindex="0" data-id="${c.id}">
        <span class="sri-name">${safeText(c.name)}</span>
        <span class="sri-id">#${c.id}</span>
      </div>
    `).join('');
    searchResults.classList.add('open');

    searchResults.querySelectorAll('.search-result-item').forEach(item => {
      const handler = () => {
        const id = parseInt(item.dataset.id);
        navigateToCard(id);
        searchResults.classList.remove('open');
        searchInput.blur();
      };
      item.addEventListener('click', handler);
      item.addEventListener('keydown', e => { if(e.key==='Enter') handler(); });
    });
  }

  function clearSearch() {
    searchQuery = '';
    searchResultIds = null;
    if (searchInput)  searchInput.value = '';
    if (searchClear)  searchClear.style.display = 'none';
    if (searchResults) searchResults.classList.remove('open');
    const hideWrap = document.getElementById('searchHideWrap');
    if (hideWrap) hideWrap.style.display = 'none';
    refreshAll();
  }

  // Close search dropdown on outside click
  document.addEventListener('click', e => {
    if (!e.target.closest('.search-wrap')) {
      searchResults && searchResults.classList.remove('open');
    }
  });

  // ===================== PAN & ZOOM =====================
  graphWrapper.addEventListener('mousedown', e => {
    if (e.target.closest('.card') || e.target.closest('line') ||
        e.target.closest('.btn') || e.target.closest('.zoom-controls') ||
        e.target.closest('.search-wrap')) return;

    isPanning = true;
    graphWrapper.classList.add('panning');
    panStart = { x: e.clientX, y: e.clientY };
    panStartOffset = { x: panX, y: panY };

    const onPan = e => {
      if (!isPanning) return;
      panX = panStartOffset.x + (e.clientX - panStart.x);
      panY = panStartOffset.y + (e.clientY - panStart.y);
      updateViewportTransform();
      refreshAll();
    };
    const stopPan = () => {
      isPanning = false;
      graphWrapper.classList.remove('panning');
      window.removeEventListener('mousemove', onPan);
      window.removeEventListener('mouseup', stopPan);
    };
    window.addEventListener('mousemove', onPan);
    window.addEventListener('mouseup', stopPan);
  });

  function updateViewportTransform() {
    const vp = document.querySelector('.graph-viewport');
    if (vp) vp.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
    const zl = document.getElementById('zoomLevel');
    if (zl) zl.textContent = `${Math.round(zoom*100)}%`;
  }

  graphWrapper.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = graphWrapper.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const factor = e.deltaY > 0 ? 0.88 : 1.14;
    const newZoom = Math.max(0.05, Math.min(3, zoom * factor));
    const ratio = newZoom / zoom;
    panX = mx - (mx - panX) * ratio;
    panY = my - (my - panY) * ratio;
    zoom = newZoom;
    updateViewportTransform();
    refreshAll();
  }, { passive: false });

  function addZoomControls() {
    const existing = document.querySelector('.zoom-controls');
    if (existing) existing.remove();
    const zc = document.createElement('div');
    zc.className = 'zoom-controls';
    zc.innerHTML = `
      <button class="zoom-btn" id="zoomInBtn">+</button>
      <div class="zoom-level" id="zoomLevel">${Math.round(zoom*100)}%</div>
      <button class="zoom-btn" id="zoomOutBtn">−</button>
      <button class="zoom-btn" id="zoomResetBtn">⟲</button>`;
    graphWrapper.appendChild(zc);

    document.getElementById('zoomInBtn').onclick = e => {
      e.stopPropagation();
      zoom = Math.min(3, zoom + 0.25); updateViewportTransform(); refreshAll();
    };
    document.getElementById('zoomOutBtn').onclick = e => {
      e.stopPropagation();
      zoom = Math.max(0.05, zoom - 0.15); updateViewportTransform(); refreshAll();
    };
    document.getElementById('zoomResetBtn').onclick = e => {
      e.stopPropagation();
      calculateOptimalZoom(); panX = 0; panY = 0; updateViewportTransform(); refreshAll();
    };
  }

  // ===================== NAVIGATION =====================
  function handleCardClick(e, cardId) {
    if (e.target.closest('.link-tag-compact')) {
      e.stopPropagation();
      const linkId = parseInt(e.target.closest('.link-tag-compact').dataset.linkId);
      if (!isNaN(linkId)) navigateToCard(linkId);
      return;
    }
    if (e.target.closest('.collapse-btn-compact')) {
      e.stopPropagation();
      collapseCard();
      return;
    }
    if (expandedCardId === cardId) {
      const card = cardMap.get(cardId);
      if (card && card.html_path) window.open(card.html_path, '_blank');
      return;
    }
    expandCard(cardId);
  }

  function expandCard(cardId) {
    expandedCardId = cardId;
    // Center on card
    const pos = nodePositions.get(cardId);
    if (pos) {
      const rect = graphWrapper.getBoundingClientRect();
      panX = rect.width  / 2 - pos.x * zoom;
      panY = rect.height / 2 - pos.y * zoom;
      updateViewportTransform();
    }
    refreshAll();
  }

  function collapseCard() {
    expandedCardId = null;
    refreshAll();
  }

  function navigateToCard(cardId) {
    if (cardMap.has(cardId)) expandCard(cardId);
  }

  // ===================== SETUP VIEWPORT =====================
  function setupViewport() {
    if (document.querySelector('.graph-viewport')) return;

    const vp = document.createElement('div');
    vp.className = 'graph-viewport';
    vp.style.cssText = 'position:absolute;top:0;left:0;width:10000px;height:10000px;transform-origin:0 0;will-change:transform;';
    graphWrapper.appendChild(vp);

    // Canvas is in screen space (graphWrapper), z-index 0 — behind everything
    if (arrowsCanvas) {
      arrowsCanvas.style.cssText = 'position:absolute;top:0;left:0;z-index:0;pointer-events:none;';
      graphWrapper.insertBefore(arrowsCanvas, graphWrapper.firstChild);
    }

    // SVG is in world space (inside viewport), z-index 1 — behind cards
    arrowsSvg.style.cssText = 'position:absolute;top:0;left:0;width:10000px;height:10000px;overflow:visible;z-index:1;pointer-events:none;';
    arrowsSvg.setAttribute('width','10000');
    arrowsSvg.setAttribute('height','10000');
    arrowsSvg.setAttribute('viewBox','0 0 10000 10000');

    // Cards on top, z-index 2
    cardsLayer.style.cssText = 'position:absolute;top:0;left:0;width:10000px;height:10000px;z-index:2;';

    vp.appendChild(arrowsSvg);
    vp.appendChild(cardsLayer);
  }

  // Фильтрация по типу связи временно отключена — будет переработана
  // document.querySelectorAll('.filter-btn').forEach(btn => {
  //   btn.addEventListener('click', () => {
  //     document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  //     btn.classList.add('active');
  //     activeFieldFilter = btn.dataset.field;
  //     collapseCard();
  //   });
  // });

  document.getElementById('refreshBtn')?.addEventListener('click', () => {
    expandedCardId = null;
    loadTopics().then(() => loadData());
  });

  window.addEventListener('resize', () => refreshAll());

  // ===================== INIT =====================
  setupViewport();
  addZoomControls();
  initApp();
})();