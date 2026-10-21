<template>
  <figure class="chart-figure">
    <div ref="container" class="data-chart" :style="{ height: height + 'px' }" role="img" :aria-label="accessibleLabel"></div>
    <details v-if="showDataTable && rows.length" class="chart-data">
      <summary>查看图表数据<span>（{{ rows.length }} 项）</span></summary>
      <div class="chart-table-wrap"><table><caption class="sr-only">{{ label || '图表' }}的数据明细</caption><thead><tr><th scope="col">系列</th><th scope="col">项目</th><th scope="col">数值</th></tr></thead><tbody><tr v-for="row in rows" :key="row.key"><th scope="row">{{ row.series }}</th><td>{{ row.item }}</td><td>{{ row.value }}</td></tr></tbody></table></div>
    </details>
  </figure>
</template>
<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, AriaComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { chartDataRows, resolveCssTokens } from '@/utils/chartTheme'
echarts.use([LineChart, BarChart, PieChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, CanvasRenderer])
const props = defineProps({ option: Object, label: String, height: { type: Number, default: 285 }, showDataTable: { type: Boolean, default: true } })
const container = ref()
const rows = computed(() => chartDataRows(props.option))
const accessibleLabel = computed(() => [props.label || '数据图表', ...rows.value.slice(0, 6).map(row => row.item + '：' + row.value), rows.value.length > 6 ? '共' + rows.value.length + '项，可展开下方数据表' : ''].filter(Boolean).join('；'))
let chart, observer, motionQuery
function render() {
  if (!chart) return
  const style = getComputedStyle(document.documentElement)
  const token = name => style.getPropertyValue(name)
  const option = resolveCssTokens(props.option || {}, token)
  chart.setOption({
    color: ['--color-accent', '--color-info', '--color-success', '--color-warning', '--color-secondary'].map(name => token(name).trim()),
    textStyle: { fontFamily: style.getPropertyValue('--font-body'), color: token('--color-secondary').trim(), fontSize: 12 },
    aria: { enabled: true },
    ...option,
    animation: !motionQuery?.matches && option.animation !== false,
  }, true)
}
onMounted(() => {
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionQuery.addEventListener('change', render)
  chart = echarts.init(container.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(container.value)
})
watch(() => props.option, render, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); motionQuery?.removeEventListener('change', render); chart?.dispose(); chart = null })
</script>
<style scoped>
.chart-figure { margin: 0; min-width: 0; }
.data-chart { width: 100%; min-width: 0; }
.chart-data { border-top: 1px solid var(--color-line); font-size: 13px; }
.chart-data summary { padding: 10px 4px; color: var(--color-secondary); cursor: pointer; width: fit-content; }
.chart-data summary span { color: var(--color-muted); font-family: var(--font-mono); margin-left: 6px; }
.chart-data summary:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.chart-table-wrap { max-height: 260px; overflow: auto; }
.chart-data table { width: 100%; border-collapse: collapse; text-align: left; }
.chart-data td, .chart-data th { padding: 8px 12px; border-top: 1px solid var(--color-line); font-weight: 400; }
.chart-data thead th { color: var(--color-secondary); background: var(--color-page); font-weight: 600; }
.chart-data td:last-child { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
</style>
