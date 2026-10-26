/* Browser-only adapter for the public GitHub Pages demo. No user data leaves the page. */
(() => {
  'use strict';
  const schemas = {
    apartments: ['id', 'name', 'region', 'area_m2', 'date', 'price_krw', 'source'],
    stocks: ['id', 'name', 'currency', 'date', 'price', 'source'],
    fx: ['date', 'krw_per_usd', 'source']
  };
  const today = () => new Date().toLocaleDateString('sv-SE');
  function positive(value, label) {
    const number = Number(value);
    if (value === '' || value == null || !Number.isFinite(number) || number <= 0 || number > 1e18)
      throw Error(`${label}: 양수를 입력해 주세요.`);
    return number;
  }
  function date(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value || value < '2000-01-01' || value > today())
      throw Error('날짜는 2000-01-01부터 오늘까지 입력해 주세요.');
    return value;
  }
  function csv(text, kind) {
    if (typeof text !== 'string' || text.length > 8_000_000) throw Error(`${kind}: CSV 파일이 필요합니다 (최대 8MB).`);
    const cells = []; let row = [], cell = '', quoted = false;
    text = text.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (quoted) {
        if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"' && cell === '') quoted = true;
      else if (ch === ',') { row.push(cell); cell = ''; }
      else if (ch === '\n') { row.push(cell); cells.push(row); row = []; cell = ''; }
      else cell += ch;
    }
    if (quoted) throw Error(`${kind}: CSV 따옴표를 확인해 주세요.`);
    if (cell || row.length) { row.push(cell); cells.push(row); }
    const headers = schemas[kind];
    if (!cells.length || cells[0].join(',') !== headers.join(',')) throw Error(`${kind} CSV 열 순서: ${headers.join(',')}`);
    if (cells.length > 50001) throw Error(`${kind}: 최대 50,000행을 지원합니다.`);
    const seen = new Set(), metadata = new Map();
    const rows = cells.slice(1).map((values, index) => {
      if (values.length !== headers.length || values.some(value => !value.trim())) throw Error(`${kind} ${index + 2}행: 빈 값 또는 열 수를 확인해 주세요.`);
      const item = Object.fromEntries(headers.map((header, i) => [header, values[i].trim()]));
      date(item.date);
      const key = `${item.id || ''}\u0000${item.date}`;
      if (seen.has(key)) throw Error(`${kind} ${index + 2}행: ID와 날짜가 중복됩니다.`);
      seen.add(key);
      for (const field of ['area_m2', 'price_krw', 'price', 'krw_per_usd'])
        if (field in item) item[field] = positive(item[field], `${kind} ${index + 2}행 ${field}`);
      if (kind === 'stocks' && !['KRW', 'USD'].includes(item.currency)) throw Error('주식 통화는 KRW 또는 USD만 가능합니다.');
      if (kind !== 'fx') {
        const identity = kind === 'apartments' ? [item.name, item.region, item.area_m2] : [item.name, item.currency];
        const signature = JSON.stringify(identity);
        if (metadata.has(item.id) && metadata.get(item.id) !== signature) throw Error(`${item.id}: 이름, 지역, 면적 또는 통화가 일치하지 않습니다.`);
        metadata.set(item.id, signature);
      }
      return item;
    });
    if (kind !== 'fx' && !rows.length) throw Error(`${kind}: 최소 1행이 필요합니다.`);
    return rows.sort((a, b) => a.date.localeCompare(b.date));
  }
  function dataset(raw) {
    if (!raw || typeof raw !== 'object') throw Error('CSV 데이터가 필요합니다.');
    return Object.fromEntries(Object.keys(schemas).map(kind => [kind, csv(raw[kind], kind)]));
  }
  function latest(rows, target, id) {
    date(target);
    const match = rows.filter(row => row.date <= target && (id === undefined || row.id === id)).at(-1);
    if (!match) throw Error(`${id || '환율'}: ${target} 이전 자료가 없습니다.`);
    return match;
  }
  function result(house, stock, fx, input = false) {
    const priceKrw = stock.price * (fx ? fx.krw_per_usd : 1);
    const shares = house.price_krw / priceKrw;
    if (!Number.isFinite(shares)) throw Error('계산 가능한 숫자 범위를 초과했습니다.');
    return { house, stock, fx, price_krw: priceKrw, shares, whole_shares: Math.ceil(shares), price_input: input, warnings: [] };
  }
  function calculate(payload, directPrice = false) {
    date(payload.house_date); date(payload.stock_date);
    if (payload.mode === 'manual') {
      if (!['KRW', 'USD'].includes(payload.currency)) throw Error('통화를 확인해 주세요.');
      const house = positive(payload.house_price, '집값');
      const price = positive(payload.price, '주가');
      const rate = payload.currency === 'USD' ? positive(payload.fx, '환율') : 1;
      const priceKrw = price * rate;
      return { manual: true, price_krw: priceKrw, shares: house / priceKrw, whole_shares: Math.ceil(house / priceKrw) };
    }
    const data = dataset(payload.data);
    const house = latest(data.apartments, payload.house_date, payload.apartment);
    const stockRow = latest(data.stocks, payload.stock_date, payload.stock);
    if (directPrice) {
      const stock = { ...stockRow, price: positive(payload.input_price, '입력 주가'), date: '사용자 입력', source: '사용자가 지정한 비교 단가' };
      const fx = stock.currency === 'USD' ? { date: '입력 환율', krw_per_usd: positive(payload.input_fx, '환율'), source: '화면의 적용 환율' } : null;
      const answer = result(house, stock, fx, true);
      answer.warnings = ['입력 가격은 가정한 비교 단가입니다. 실제 종가로 확인되지 않았습니다.'];
      return answer;
    }
    const fx = stockRow.currency === 'USD' ? latest(data.fx, stockRow.date) : null;
    const answer = result(house, stockRow, fx);
    for (const [label, observed, requested] of [['아파트', house.date, payload.house_date], ['주가', stockRow.date, payload.stock_date], ...(fx ? [['환율', fx.date, stockRow.date]] : [])]) {
      const days = Math.round((Date.parse(requested) - Date.parse(observed)) / 86400000);
      if (days) answer.warnings.push(`${label}: 기준일보다 ${days.toLocaleString('ko-KR')}일 이전 자료를 사용했습니다.`);
    }
    return answer;
  }
  async function demo() {
    const values = await Promise.all(Object.keys(schemas).map(async kind => {
      const response = await fetch(`data/${kind}.csv`);
      if (!response.ok) throw Error('예시 CSV를 불러오지 못했습니다.');
      return [kind, await response.text()];
    }));
    return Object.fromEntries(values);
  }
  async function api(path, payload) {
    if (path === '/api/demo') return demo();
    if (path === '/api/calculate') return calculate(payload);
    if (path === '/api/price-compare') return calculate(payload, true);
    const data = dataset(payload?.data);
    if (path === '/api/catalog') return {
      apartments: [...new Map(data.apartments.map(row => [row.id, row])).values()],
      stocks: [...new Map(data.stocks.map(row => [row.id, row])).values()],
      ranges: Object.fromEntries(Object.entries(data).map(([kind, rows]) => [kind, rows.length ? { start: rows[0].date, end: rows.at(-1).date, count: rows.length } : null]))
    };
    if (path === '/api/stock-history') {
      const rows = data.stocks.filter(row => row.id === payload.stock);
      return { stock: rows.at(-1) || null, rows, fx: data.fx.at(-1) || null };
    }
    throw Error('공개 데모에서 사용할 수 없는 기능입니다.');
  }
  window.staticApi = api;
})();
