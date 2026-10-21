// Resolve design tokens before ECharts sends colors to a canvas, which cannot use CSS var().
export function resolveCssTokens(value, readToken) {
  if (typeof value === 'string') {
    const match = value.trim().match(/^var\(\s*(--[a-zA-Z0-9-]+)\s*(?:,\s*([^)]*))?\)$/)
    return match ? readToken(match[1]).trim() || match[2]?.trim() || value : value
  }
  if (Array.isArray(value)) return value.map(item => resolveCssTokens(item, readToken))
  if (value && typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveCssTokens(item, readToken)]))
  }
  return value
}

// An equivalent, complete data table for the line/bar/pie options used in this app.
export function chartDataRows(option = {}) {
  const series = Array.isArray(option.series) ? option.series : option.series ? [option.series] : []
  const xAxis = Array.isArray(option.xAxis) ? option.xAxis[0] : option.xAxis
  const yAxis = Array.isArray(option.yAxis) ? option.yAxis[0] : option.yAxis
  const categories = xAxis?.data || yAxis?.data || []
  return series.flatMap((entry, seriesIndex) => (entry.data || []).map((item, index) => {
    const raw = item !== null && typeof item === 'object' && !Array.isArray(item) ? item.value : item
    const name = item !== null && typeof item === 'object' && !Array.isArray(item) ? item.name : undefined
    return {
      key: seriesIndex + '-' + index,
      series: entry.name || '数据',
      item: String(name ?? categories[index] ?? (Array.isArray(raw) ? raw[0] : index + 1)),
      value: Array.isArray(raw) ? raw.slice(1).map(v => v ?? '—').join(' / ') : String(raw ?? '—'),
    }
  }))
}
