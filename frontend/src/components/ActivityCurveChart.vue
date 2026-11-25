<template>
  <div class="acc-chart-wrap">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="xMidYMid meet"
         class="acc-chart" role="img" aria-label="接收日与服务时点活动曲线">
      <!-- gridlines + y labels -->
      <g v-for="g in grid" :key="g.i">
        <line :x1="pad.l" :x2="W - pad.r" :y1="g.y" :y2="g.y"
              stroke="#e8edf6" stroke-width="1" />
        <text :x="pad.l - 7" :y="g.y + 3.5" text-anchor="end"
              class="acc-axis-text">{{ g.v }}</text>
      </g>

      <!-- received bars -->
      <g v-if="showReceived">
        <rect v-for="(v, i) in received" :key="'b' + i"
              :x="x(i) - barW / 2" :y="y(v)" :width="barW"
              :height="Math.max(0, pad.b + plotH - y(v))"
              :fill="barColor" rx="1.5">
          <title>{{ labels[i] }} 签收/接收 {{ v }} 笔</title>
        </rect>
      </g>

      <!-- deconvolution uncertainty band -->
      <path v-if="showDeconv && bandPath" :d="bandPath"
            fill="rgba(124,58,237,0.13)" stroke="none" />

      <!-- batch reconciliation line -->
      <path v-if="showBatch && batchPath" :d="batchPath" fill="none"
            stroke="#0f766e" stroke-width="2" stroke-dasharray="6 3" />

      <!-- deconv estimate line -->
      <path v-if="showDeconv && deconvPath" :d="deconvPath" fill="none"
            stroke="#7c3aed" stroke-width="2" stroke-dasharray="2 3"
            stroke-linecap="round" />

      <!-- direct service-time line -->
      <path v-if="servicePath" :d="servicePath" fill="none"
            stroke="#1d4ed8" stroke-width="2.6" stroke-linejoin="round"
            stroke-linecap="round" />
      <g v-if="servicePath">
        <circle v-for="(v, i) in service" :key="'s' + i"
                v-show="v !== null && v !== undefined"
                :cx="x(i)" :cy="y(Number(v))" r="2.6" fill="#1d4ed8">
          <title>{{ labels[i] }} 按服务时点 {{ v }} 笔</title>
        </circle>
      </g>

      <!-- x labels -->
      <text v-for="(lb, i) in xLabels" :key="'x' + i"
            :x="x(lb.i)" :y="H - 14" text-anchor="middle"
            class="acc-axis-text">{{ lb.text }}</text>
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  labels: { type: Array, default: () => [] },
  received: { type: Array, default: () => [] },
  service: { type: Array, default: () => [] },
  deconv: { type: Array, default: () => [] },   // [{e,lo,hi}|null]
  batch: { type: Array, default: () => [] },     // number|null
  showReceived: { type: Boolean, default: true },
  showService: { type: Boolean, default: true },
  showDeconv: { type: Boolean, default: false },
  showBatch: { type: Boolean, default: false },
  barColor: { type: String, default: 'rgba(220,38,38,0.42)' }
})

const W = 1000
const H = 360
const pad = { l: 46, r: 14, t: 16, b: 34 }
const plotW = W - pad.l - pad.r
const plotH = H - pad.t - pad.b

const n = computed(() => props.labels.length || 1)
const barW = computed(() => Math.max(3, Math.min(16, (plotW / n.value) * 0.62)))

function x(i) {
  const step = plotW / n.value
  return pad.l + step * i + step / 2
}
const yMax = computed(() => {
  let m = 4
  props.received.forEach(v => { m = Math.max(m, Number(v) || 0) })
  props.service.forEach(v => { if (v != null) m = Math.max(m, Number(v) || 0) })
  if (props.showDeconv) props.deconv.forEach(d => { if (d) m = Math.max(m, d.hi || 0) })
  if (props.showBatch) props.batch.forEach(v => { if (v != null) m = Math.max(m, Number(v) || 0) })
  return Math.ceil(m * 1.12 / 10) * 10
})
function y(v) { return pad.t + plotH * (1 - (Number(v) || 0) / yMax.value) }

const grid = computed(() => {
  const steps = 4
  const out = []
  for (let g = 0; g <= steps; g++) {
    const v = Math.round((yMax.value / steps) * g)
    out.push({ i: g, v, y: y(v) })
  }
  return out
})

const xLabels = computed(() => {
  const out = []
  const every = n.value > 24 ? 2 : 1
  props.labels.forEach((iso, i) => {
    const day = Number(iso.slice(8, 10))
    // 每隔 every 天且尽量取奇数日，避免拥挤；1 号必标
    if (i % every === 0 || day === 1) {
      out.push({ i, text: iso.slice(5).replace('-', '/') })
    }
  })
  return out
})

function linePath(values) {
  let d = ''
  let pen = false
  values.forEach((v, i) => {
    if (v === null || v === undefined || Number.isNaN(Number(v))) { pen = false; return }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `
    pen = true
  })
  return d.trim()
}
const servicePath = computed(() => props.showService ? linePath(props.service) : '')
const deconvPath = computed(() =>
  props.showDeconv ? linePath(props.deconv.map(d => (d ? d.e : null))) : '')
const batchPath = computed(() =>
  props.showBatch ? linePath(props.batch.map(v => (v == null ? null : v))) : '')

const bandPath = computed(() => {
  if (!props.showDeconv || !props.deconv.length) return ''
  const upper = [], lower = []
  props.deconv.forEach((d, i) => {
    if (!d) return
    upper.push([x(i), y(d.hi)])
    lower.push([x(i), y(d.lo)])
  })
  if (upper.length < 2) return ''
  let p = `M${upper[0][0].toFixed(1)},${upper[0][1].toFixed(1)} `
  upper.slice(1).forEach(pt => { p += `L${pt[0].toFixed(1)},${pt[1].toFixed(1)} ` })
  lower.reverse().forEach(pt => { p += `L${pt[0].toFixed(1)},${pt[1].toFixed(1)} ` })
  return p + 'Z'
})
</script>

<style scoped>
.acc-chart-wrap { width: 100%; overflow-x: auto; }
.acc-chart { width: 100%; min-width: 720px; height: auto; aspect-ratio: 1000 / 360; display: block; }
.acc-axis-text { font-size: 11px; fill: #94a3b8; }
</style>
