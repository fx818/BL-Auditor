(function () {
  var LS_AGENTS   = 'bl_auditor_agent_prefs';
  var LS_CARDS    = 'bl_auditor_card_prefs';      // legacy — ignored on read
  var LS_CARDS_V2 = 'bl_auditor_card_prefs_v2';  // stores hidden keys (not visible)

  var ALL_AGENTS = [
    { key: 'auditmate',         label: 'Mismatch Agents (Title vs Category / Title vs Specs / Specs vs Category)' },
    { key: 'buyer_viewed',      label: 'Buyer Viewed' },
    { key: 'retail_agent_2',    label: 'Retail Classification (Agent 2)' },
    { key: 'isq',               label: 'ISQ Validation' },
    { key: 'description',       label: 'Description Coherence' },
    { key: 'specs_vs_category', label: 'Specs vs Category (Agent 2)' },
    { key: 'title_vs_category', label: 'Title vs Category (Agent 2)' },
    { key: 'title_vs_specs',    label: 'Title vs Specs (Agent 2)' },
    { key: 'buyer_profile',         label: 'Buyer Profile' },
    { key: 'buyer_viewed_vs_csl',   label: 'Buyer Viewed vs CSL' },
  ];

  var ALL_CARDS = [
    { key: 'buylead',          label: 'BuyLead Info (sidebar)' },
    { key: 'decision',         label: 'Decision Agent (sidebar)' },
    { key: 'score_1',          label: 'BL Score 1 (sidebar)' },
    { key: 'score_2',          label: 'BL Score 2 (sidebar)' },
    { key: 'completeness',     label: 'Completeness Score (sidebar)' },
    { key: 'category_checks',  label: 'Category Checks (Mismatch Agents)' },
    { key: 'agent2_checks',    label: 'Agent 2 Checks (Specs/Title vs Category/Specs)' },
    { key: 'buyer_viewed',          label: 'Buyer Viewed' },
    { key: 'buyer_profile',         label: 'Buyer Profile' },
    { key: 'buyer_viewed_vs_csl',   label: 'Buyer Viewed vs CSL' },
    { key: 'retail_agent_2',   label: 'Retail Classification (Agent 2)' },
    { key: 'isq_validation',   label: 'ISQ Validation' },
    { key: 'description',      label: 'Description Coherence' },
    { key: 'quantity_audit',   label: 'Quantity Audit' },
    { key: 'buyer_activity',   label: 'Buyer Activity Timeline' },
  ];

  function _allKeys(list) { return list.map(function (x) { return x.key; }); }

  function getAgentPrefs() {
    try {
      var raw = localStorage.getItem(LS_AGENTS);
      if (!raw) return new Set(_allKeys(ALL_AGENTS));
      return new Set(JSON.parse(raw));
    } catch (e) {
      return new Set(_allKeys(ALL_AGENTS));
    }
  }

  function setAgentPrefs(keys) {
    localStorage.setItem(LS_AGENTS, JSON.stringify(Array.from(keys)));
  }

  function getCardPrefs() {
    try {
      var allKeys = _allKeys(ALL_CARDS);
      var raw = localStorage.getItem(LS_CARDS_V2);
      if (!raw) return new Set(allKeys);
      var hidden = new Set(JSON.parse(raw));
      // Only hide keys the user explicitly unchecked; new ALL_CARDS keys default to visible.
      return new Set(allKeys.filter(function (k) { return !hidden.has(k); }));
    } catch (e) {
      return new Set(_allKeys(ALL_CARDS));
    }
  }

  function setCardPrefs(keys) {
    // Store hidden keys so new cards default to visible without clobbering user choices.
    var hidden = _allKeys(ALL_CARDS).filter(function (k) { return !keys.has(k); });
    localStorage.setItem(LS_CARDS_V2, JSON.stringify(hidden));
  }

  // Which card keys to force-hide when a given agent is deselected.
  var AGENT_TO_CARDS = {
    'auditmate':            ['category_checks'],
    'buyer_viewed':         ['buyer_viewed'],
    'retail_agent_2':       ['retail_agent_2'],
    'isq':                  ['isq_validation'],
    'description':          ['description'],
    'buyer_profile':        ['buyer_profile'],
    'buyer_viewed_vs_csl':  ['buyer_viewed_vs_csl'],
  };
  // agent2_checks is visible only when at least one of these three agents is selected.
  var AGENT2_KEYS = ['specs_vs_category', 'title_vs_category', 'title_vs_specs'];

  function applyCardVisibility() {
    var cardPrefs  = getCardPrefs();
    var agentPrefs = getAgentPrefs();

    // Build set of cards that are force-hidden because their agent wasn't selected.
    var forceHidden = new Set();
    Object.keys(AGENT_TO_CARDS).forEach(function (agentKey) {
      if (!agentPrefs.has(agentKey)) {
        AGENT_TO_CARDS[agentKey].forEach(function (cardKey) { forceHidden.add(cardKey); });
      }
    });
    var anyAgent2 = AGENT2_KEYS.some(function (k) { return agentPrefs.has(k); });
    if (!anyAgent2) forceHidden.add('agent2_checks');

    document.querySelectorAll('[data-card]').forEach(function (el) {
      var key = el.getAttribute('data-card');
      el.style.display = (cardPrefs.has(key) && !forceHidden.has(key)) ? '' : 'none';
    });
  }

  window.BLSettings = {
    ALL_AGENTS: ALL_AGENTS,
    ALL_CARDS: ALL_CARDS,
    getAgentPrefs: getAgentPrefs,
    setAgentPrefs: setAgentPrefs,
    getCardPrefs: getCardPrefs,
    setCardPrefs: setCardPrefs,
    applyCardVisibility: applyCardVisibility,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyCardVisibility);
  } else {
    applyCardVisibility();
  }
})();
