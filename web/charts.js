// charts.js — themed ECharts wrappers

const PALETTE = ['#4A9EFF', '#7C5CFF', '#3FB68B', '#E8A23B', '#E5484D', '#5BCEDA', '#F472B6'];

const BASE = {
  textStyle: { color: '#E6EDF3', fontFamily: 'Inter' },
  color: PALETTE,
  grid: { left: 36, right: 12, top: 24, bottom: 24, containLabel: true },
};

const X_AXIS = {
  axisLine:  { lineStyle: { color: '#1F2630' } },
  axisLabel: { color: '#8B98A6' },
  axisTick:  { show: false },
};

const Y_AXIS = {
  axisLine:  { show: false },
  axisTick:  { show: false },
  splitLine: { lineStyle: { color: '#1F2630' } },
  axisLabel: { color: '#8B98A6' },
};

const TOOLTIP = {
  trigger: 'axis',
  backgroundColor: '#0F1419',
  borderColor: '#283040',
  borderWidth: 1,
  textStyle: { color: '#E6EDF3', fontFamily: 'Inter', fontSize: 12 },
  padding: [8, 12],
};

function mount(el) {
  const c = echarts.init(el, null, { renderer: 'svg' });
  window.addEventListener('resize', () => c.resize());
  return c;
}

export function lineChart(el, { x, series }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: TOOLTIP,
    legend: { textStyle: { color: '#8B98A6' }, top: 0, right: 0, icon: 'roundRect', itemWidth: 8, itemHeight: 8 },
    xAxis: { ...X_AXIS, type: 'category', data: x, boundaryGap: false },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map(s => ({
      ...s, type: 'line', smooth: true, showSymbol: false,
      areaStyle: { opacity: 0.12 }, lineStyle: { width: 2 },
    })),
  });
  return c;
}

export function barChart(el, { categories, values, color }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { ...X_AXIS, type: 'category', data: categories, axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 } },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: [{
      type: 'bar', data: values,
      itemStyle: { color: color || PALETTE[0], borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 32,
    }],
  });
  return c;
}

export function hBarChart(el, { categories, values, color }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    grid: { left: 12, right: 16, top: 8, bottom: 8, containLabel: true },
    tooltip: { ...TOOLTIP, axisPointer: { type: 'shadow' } },
    xAxis: { ...Y_AXIS, type: 'value' },
    yAxis: {
      ...X_AXIS, type: 'category',
      data: [...categories].reverse(),
      axisLabel: { ...X_AXIS.axisLabel, width: 120, overflow: 'truncate' },
    },
    series: [{
      type: 'bar', data: [...values].reverse(),
      itemStyle: { color: color || PALETTE[0], borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 20,
      label: { show: true, position: 'right', color: '#8B98A6', fontSize: 11,
               formatter: p => p.value.toLocaleString() },
    }],
  });
  return c;
}

export function stackedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: {
      textStyle: { color: '#8B98A6' },
      top: 0, right: 0, icon: 'roundRect',
      itemWidth: 8, itemHeight: 8,
    },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: categories.length > 20 ? 'auto' : 0, rotate: categories.length > 12 ? 45 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      stack: 'total',
      data: s.values,
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length] },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

export function groupedBarChart(el, { categories, series, formatter }) {
  const c = mount(el);
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      axisPointer: { type: 'shadow' },
      valueFormatter: formatter || (v => Number(v).toLocaleString()),
    },
    legend: {
      textStyle: { color: '#8B98A6' },
      top: 0, right: 0, icon: 'roundRect',
      itemWidth: 8, itemHeight: 8,
    },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: 0, rotate: categories.length > 5 ? 25 : 0 },
    },
    yAxis: { ...Y_AXIS, type: 'value' },
    series: series.map((s, i) => ({
      name: s.name,
      type: 'bar',
      data: s.values,
      itemStyle: { color: s.color || PALETTE[i % PALETTE.length], borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 24,
      emphasis: { focus: 'series' },
    })),
  });
  return c;
}

export function donutChart(el, data) {
  const c = mount(el);
  c.setOption({
    color: PALETTE,
    tooltip: {
      trigger: 'item',
      backgroundColor: '#0F1419', borderColor: '#283040', borderWidth: 1,
      textStyle: { color: '#E6EDF3', fontFamily: 'Inter' },
      formatter: p => `${p.name}<br/><b>${Number(p.value).toLocaleString()}</b> tokens (${p.percent.toFixed(1)}%)`,
    },
    legend: {
      textStyle: { color: '#8B98A6' },
      bottom: 10, icon: 'roundRect', itemWidth: 8, itemHeight: 8,
      type: 'scroll',
    },
    series: [{
      type: 'pie',
      center: ['50%', '44%'],
      radius: ['48%', '68%'],
      avoidLabelOverlap: true,
      padAngle: 2,
      itemStyle: { borderColor: '#0F1419', borderWidth: 2, borderRadius: 4 },
      label: {
        show: true,
        position: 'inside',
        color: '#fff',
        fontSize: 12,
        fontWeight: 600,
        formatter: ({ percent }) => percent >= 6 ? percent.toFixed(0) + '%' : '',
      },
      labelLine: { show: false },
      data,
    }],
  });
  return c;
}

export function mixedBarLineChart(el, { categories, bar, line, barLabel, lineLabel, formatter }) {
  // bar series = actual (bars), line series = estimate (line overlay), shared tooltip
  const c = mount(el);
  const fmt = formatter || (v => '$' + Number(v).toFixed(4));
  c.setOption({
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const date = params[0]?.axisValue || '';
        return params.map(p =>
          `${p.marker} ${p.seriesName}: ${fmt(p.value)}`
        ).join('<br>');
      },
    },
    legend: {
      textStyle: { color: '#8B98A6' }, top: 0, right: 0,
      icon: 'roundRect', itemWidth: 8, itemHeight: 8,
    },
    xAxis: {
      ...X_AXIS, type: 'category', data: categories,
      axisLabel: { ...X_AXIS.axisLabel, interval: categories.length > 20 ? 'auto' : 0, rotate: categories.length > 12 ? 45 : 0 },
    },
    yAxis: [
      { ...Y_AXIS, type: 'value', name: '' },
    ],
    series: [
      {
        name: barLabel || 'actual',
        type: 'bar',
        data: bar,
        itemStyle: { color: '#4A9EFF', borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 20,
      },
      {
        name: lineLabel || 'estimate',
        type: 'line',
        data: line,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#E8A23B', width: 2, type: 'dashed' },
        itemStyle: { color: '#E8A23B' },
      },
    ],
  });
  return c;
}
