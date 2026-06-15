// i18n.js — EN / PT-BR translation dictionary
const DICT = {
  en: {
    // nav
    nav_overview:'overview', nav_prompts:'prompts', nav_sessions:'sessions',
    nav_projects:'projects', nav_skills:'skills', nav_tips:'tips', nav_settings:'settings',
    blur_hint:'⌘B blur',
    // welcome
    wl_title:'Welcome — pick your plan',
    wl_sub:'This sets how costs are displayed. Change it later in Settings.',
    wl_continue:'Continue',
    // ranges / sorts
    range_all:'All', sort_tokens:'Most tokens', sort_recent:'Most recent',
    // overview
    ov_title:'Overview', ov_last_days:'last {d} days', ov_all_time:'all time',
    kpi_sessions:'Sessions', kpi_turns:'Turns', kpi_input:'Input', kpi_output:'Output',
    kpi_cache_read:'Cache read', kpi_cache_create:'Cache create', kpi_cost:'Est. cost',
    plan_pay:'pay ${m}/mo on {l}',
    ov_explain:'What do these numbers mean?', ov_expand:'— click to expand',
    session_dt:'Session', session_dd:'One run of Claude Code (from <code>claude</code> to exit). Each session is a single <code>.jsonl</code> file.',
    turn_dt:'Turn', turn_dd:'One message you sent to Claude. Each turn triggers a response (possibly with tool calls in between).',
    input_dt:'Input tokens', input_dd:'The new text you (and tool results) sent to Claude this turn. Billed at the full input rate.',
    output_dt:'Output tokens', output_dd:'The text Claude wrote back. Billed at the highest rate — usually the biggest cost driver per turn.',
    cread_dt:'Cache read', cread_dd:'Tokens Claude re-used from a cache (your CLAUDE.md, previously-read files, the conversation so far). ~10× cheaper than fresh input. High cache-read counts = good cost hygiene.',
    ccreate_dt:'Cache create', ccreate_dd:'Writing something into the cache for the first time. One-time cost; pays off on the next turn.',
    billable_dt:'Billable tokens', billable_dd:'Input + Output + Cache create. Cache reads are billed separately (and much cheaper).',
    daily_work:'Your daily work',
    daily_work_sub:'Tokens you paid for: what you sent (<b>input</b>), what Claude wrote (<b>output</b>), and what got stored for re-use (<b>cache create</b>).',
    daily_cache:'Daily cache reads',
    daily_cache_sub:'<b>Cache reads</b> are cheap re-uses of things Claude already saw (like your CLAUDE.md). They cost ~10× less than regular input tokens — high numbers here are a good thing.',
    by_project:'Tokens by project', by_model:'Token usage by model', by_model_sub:'Share of billable tokens per Claude model.',
    top_tools:'Top tools (by call count)', recent_sessions:'Recent sessions', all_arr:'all →',
    col_started:'started', col_project:'project', col_tokens:'tokens',
    no_sessions:'no sessions in this range',
    leg_input:'input', leg_output:'output', leg_cc:'cache create', leg_cr:'cache read',
    // prompts
    pr_title:'Prompts',
    pr_sub_recent:'Your latest prompts and the assistant turn each one triggered. Click a row to see the full prompt.',
    pr_sub_tokens:'The prompts that cost the most tokens. Click a row to see the full prompt.',
    pr_col_cost:'cache cost', pr_col_when:'when', pr_col_prompt:'prompt', pr_col_model:'model',
    pr_col_tokens:'tokens', pr_col_crd:'cache rd', pr_col_session:'session',
    pr_no_prompts:'no prompts yet', pr_detail:'Prompt detail',
    pr_billable:'billable', pr_crd:'cache rd', pr_ccost:'cache cost', pr_open:'Open session →',
    // sessions
    se_title:'Sessions',
    se_col_started:'started', se_col_project:'project', se_col_turns:'turns',
    se_col_tokens:'tokens', se_col_session:'session', se_records:'records',
    se_tbt:'Turn-by-turn', se_col_time:'time', se_col_type:'type', se_col_pt:'prompt / tools',
    se_col_in:'in', se_col_out:'out', se_col_crd:'cache rd', se_all:'← all sessions',
    se_in:'in', se_out:'out', se_crd:'cache rd',
    // projects
    pj_title:'Projects',
    pj_sub:'Sorted by billable token spend. Cache reads are billed cheaper, so high cache-read columns are good.',
    pj_col_project:'project', pj_col_sessions:'sessions', pj_col_turns:'turns',
    pj_col_billable:'billable tokens', pj_col_cache:'cache reads',
    // skills
    sk_title:'Skills', sk_unique:'Unique skills used', sk_total:'Total invocations',
    sk_top:'Top skills (by invocations)', sk_all:'All skills',
    sk_sub:'"Tokens per call" is the size of the skill\'s <code>SKILL.md</code> file — what Claude Code loads into context each time the skill is invoked.',
    sk_col_skill:'skill', sk_col_inv:'invocations', sk_col_tpc:'tokens per call',
    sk_col_sess:'sessions', sk_col_last:'last used', sk_no:'no skills invoked in this range',
    // tips
    ti_title:'Suggestions',
    ti_empty:'No suggestions right now. Token Dashboard surfaces patterns weekly — check back after more activity.',
    ti_sub:'Rule-based pattern detection over the last 7 days. Dismissed tips re-appear after 14 days.',
    ti_dismiss:'dismiss',
    // settings
    st_title:'Settings', st_plan:'Plan',
    st_plan_sub:'Sets how cost is displayed. API mode shows pay-per-token rates. Subscription modes show what you actually pay each month.',
    st_save:'Save', st_saved:'Saved.', st_pricing:'Pricing table',
    st_pricing_sub:'Edit <code>pricing.json</code> in the project root to change rates. Reload the page after editing.',
    st_col_model:'model', st_col_in:'input', st_col_out:'output',
    st_col_cr:'cache read', st_col_c5m:'cache 5m', st_col_c1h:'cache 1h',
    st_rates:'Rates per 1M tokens, USD.',
    st_privacy:'Privacy',
    st_privacy_body:'Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.',
  },
  pt: {
    // nav
    nav_overview:'visão geral', nav_prompts:'prompts', nav_sessions:'sessões',
    nav_projects:'projetos', nav_skills:'skills', nav_tips:'dicas', nav_settings:'configurações',
    blur_hint:'⌘B ocultar',
    // welcome
    wl_title:'Bem-vindo — escolha seu plano',
    wl_sub:'Define como os custos são exibidos. Altere depois em Configurações.',
    wl_continue:'Continuar',
    // ranges / sorts
    range_all:'Todos', sort_tokens:'Mais tokens', sort_recent:'Mais recentes',
    // overview
    ov_title:'Visão Geral', ov_last_days:'últimos {d} dias', ov_all_time:'todo período',
    kpi_sessions:'Sessões', kpi_turns:'Interações', kpi_input:'Entrada', kpi_output:'Saída',
    kpi_cache_read:'Cache lido', kpi_cache_create:'Cache criado', kpi_cost:'Custo Est.',
    plan_pay:'você paga ${m}/mês no {l}',
    ov_explain:'O que esses números significam?', ov_expand:'— clique para expandir',
    session_dt:'Sessão', session_dd:'Uma execução do Claude Code, desde o comando <code>claude</code> até a saída. Cada sessão corresponde a um único arquivo <code>.jsonl</code>.',
    turn_dt:'Interação', turn_dd:'Uma mensagem enviada por você ao Claude. Cada interação gera uma resposta, possivelmente com chamadas de ferramentas durante o processo.',
    input_dt:'Tokens de entrada', input_dd:'O novo texto enviado ao Claude nesta interação, incluindo suas mensagens e os resultados de ferramentas. São cobrados pela taxa integral de entrada.',
    output_dt:'Tokens de saída', output_dd:'O texto que o Claude escreveu como resposta. São cobrados pela taxa mais alta e, geralmente, representam o maior custo por interação.',
    cread_dt:'Cache lido', cread_dd:'Tokens que o Claude reutilizou do cache, como o conteúdo do <code>CLAUDE.md</code>, arquivos lidos anteriormente ou o histórico da conversa. Costumam ser cerca de 10 vezes mais baratos que tokens de entrada novos. Valores altos de cache lido indicam uma boa prática de controle de custos.',
    ccreate_dt:'Cache criado', ccreate_dd:'Tokens gravados no cache pela primeira vez. É um custo único, que tende a compensar nas próximas interações.',
    billable_dt:'Tokens faturáveis', billable_dd:'Tokens de entrada + tokens de saída + cache criado. O cache lido também é cobrado, mas separadamente e com custo muito menor.',
    daily_work:'Seu trabalho diário',
    daily_work_sub:'Tokens que você pagou: o que enviou (<b>entrada</b>), o que o Claude escreveu (<b>saída</b>) e o que foi armazenado para reuso (<b>cache criado</b>).',
    daily_cache:'Leituras de cache diárias',
    daily_cache_sub:'<b>Leituras de cache</b> são reusos baratos de coisas que o Claude já viu (como seu CLAUDE.md). Custam ~10× menos que tokens de entrada normais — números altos aqui são positivos.',
    by_project:'Tokens por projeto', by_model:'Uso de tokens por modelo', by_model_sub:'Parcela dos tokens faturáveis por modelo Claude.',
    top_tools:'Ferramentas mais usadas (por chamadas)', recent_sessions:'Sessões recentes', all_arr:'todas →',
    col_started:'início', col_project:'projeto', col_tokens:'tokens',
    no_sessions:'sem sessões neste período',
    leg_input:'entrada', leg_output:'saída', leg_cc:'cache criado', leg_cr:'cache lido',
    // prompts
    pr_title:'Prompts',
    pr_sub_recent:'Seus últimos prompts e o turno do assistente. Clique em uma linha para ver o prompt completo.',
    pr_sub_tokens:'Os prompts que custaram mais tokens. Clique em uma linha para ver o prompt completo.',
    pr_col_cost:'custo cache', pr_col_when:'quando', pr_col_prompt:'prompt', pr_col_model:'modelo',
    pr_col_tokens:'tokens', pr_col_crd:'cache lido', pr_col_session:'sessão',
    pr_no_prompts:'nenhum prompt ainda', pr_detail:'Detalhe do prompt',
    pr_billable:'faturável', pr_crd:'cache lido', pr_ccost:'custo cache', pr_open:'Abrir sessão →',
    // sessions
    se_title:'Sessões',
    se_col_started:'início', se_col_project:'projeto', se_col_turns:'turnos',
    se_col_tokens:'tokens', se_col_session:'sessão', se_records:'registros',
    se_tbt:'Turno a turno', se_col_time:'hora', se_col_type:'tipo', se_col_pt:'prompt / ferramentas',
    se_col_in:'entrada', se_col_out:'saída', se_col_crd:'cache lido', se_all:'← todas as sessões',
    se_in:'entrada', se_out:'saída', se_crd:'cache lido',
    // projects
    pj_title:'Projetos',
    pj_sub:'Ordenado por gasto de tokens faturáveis. Leituras de cache são mais baratas, portanto colunas altas são positivas.',
    pj_col_project:'projeto', pj_col_sessions:'sessões', pj_col_turns:'turnos',
    pj_col_billable:'tokens faturáveis', pj_col_cache:'cache lidos',
    // skills
    sk_title:'Skills', sk_unique:'Skills únicas usadas', sk_total:'Total de invocações',
    sk_top:'Top skills (por invocações)', sk_all:'Todas as skills',
    sk_sub:'"Tokens por chamada" é o tamanho do arquivo <code>SKILL.md</code> da skill — o que o Claude Code carrega no contexto a cada invocação.',
    sk_col_skill:'skill', sk_col_inv:'invocações', sk_col_tpc:'tokens por chamada',
    sk_col_sess:'sessões', sk_col_last:'último uso', sk_no:'nenhuma skill invocada neste período',
    // tips
    ti_title:'Sugestões',
    ti_empty:'Sem sugestões por enquanto. O Token Dashboard detecta padrões semanalmente — volte após mais atividade.',
    ti_sub:'Detecção de padrões nos últimos 7 dias. Dicas dispensadas reaparecem após 14 dias.',
    ti_dismiss:'dispensar',
    // settings
    st_title:'Configurações', st_plan:'Plano',
    st_plan_sub:'Define como o custo é exibido. O modo API mostra as taxas por token. Os modos de assinatura mostram o que você realmente paga por mês.',
    st_save:'Salvar', st_saved:'Salvo.', st_pricing:'Tabela de preços',
    st_pricing_sub:'Edite <code>pricing.json</code> na raiz do projeto para alterar as taxas. Recarregue após editar.',
    st_col_model:'modelo', st_col_in:'entrada', st_col_out:'saída',
    st_col_cr:'cache lido', st_col_c5m:'cache 5m', st_col_c1h:'cache 1h',
    st_rates:'Taxas por 1M tokens, USD.',
    st_privacy:'Privacidade',
    st_privacy_body:'Pressione <code>Cmd/Ctrl + B</code> em qualquer lugar para ocultar prompts e conteúdos sensíveis.',
  },
};

export function getLocale() { return localStorage.getItem('td.locale') || 'en'; }
export function setLocale(l) { localStorage.setItem('td.locale', l); }
export function t(key) {
  const d = DICT[getLocale()];
  return (d && d[key] !== undefined ? d[key] : DICT.en[key]) ?? key;
}
