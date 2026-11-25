<template>
  <div class="page acc-page">
    <h2 class="page-title">服务活动时间重建</h2>
    <p class="page-sub">
      结算平台常在周末/月末批量补传；若按平台签收（接收）日统计，原本按服务时点分散的诊疗会叠加成人为高峰、
      甚至触发错误线索。本页从<b>来源画像</b>（机构 · 清算批次 · 服务发生时间 · 签收时间 · 批次内序号）出发，
      用<b>分机构经验延迟反卷积</b>与<b>清算批次清单对账</b>两条路径，重建同一目标期间（2026-03）的日级
      就诊/结算活动并报告不确定范围，再把<b>真实服务峰</b>与<b>传输批次峰</b>分离。
    </p>

    <div class="disclaimer-banner">
      样例均为虚构数据。重建结果只用于校正统计口径与核查优先级，<b>不构成欺诈或违法结论</b>；
      缺失服务时间一律保留为“未知”，不会被分配到月末。
      <el-button link type="primary" @click="$router.push('/dashboard')">前往总览运行一键验收 →</el-button>
    </div>

    <!-- 控制条 -->
    <div class="card">
      <div class="flex-between" style="flex-wrap:wrap;gap:10px">
        <div class="flex gap8" style="align-items:center;flex-wrap:wrap">
          <span class="muted small">机构范围</span>
          <el-select v-model="org" style="width:280px" @change="loadRecon">
            <el-option label="全部机构 A+B+C（汇总）" value="ALL" />
            <el-option v-for="p in profiles" :key="p.org_code"
                       :label="`${p.org_code} · ${p.report_mode_label}`" :value="p.org_code" />
          </el-select>
          <el-radio-group v-model="kind" @change="loadRecon">
            <el-radio-button value="visit">就诊活动</el-radio-button>
            <el-radio-button value="settlement">结算活动</el-radio-button>
          </el-radio-group>
        </div>
        <div class="flex gap8" style="align-items:center;flex-wrap:wrap">
          <el-checkbox v-model="showReceived">签收/接收柱</el-checkbox>
          <el-checkbox v-model="showService">服务时点曲线</el-checkbox>
          <el-checkbox v-model="showDeconv">反卷积+区间</el-checkbox>
          <el-checkbox v-model="showBatch">批次对账线</el-checkbox>
        </div>
      </div>

      <!-- KPI -->
      <div class="kpi-grid" v-if="recon">
        <div class="kpi kpi-red">
          <div class="kpi-n">{{ recon.peaks.raw_peak.count }}</div>
          <div class="kpi-l">接收日峰值（{{ recon.peaks.raw_peak.date.slice(5) }}）</div>
        </div>
        <div class="kpi kpi-blue">
          <div class="kpi-n">{{ recon.peaks.raw_peak_corrected_same_day ?? '—' }}</div>
          <div class="kpi-l">该签收日实际服务量</div>
        </div>
        <div class="kpi kpi-green">
          <div class="kpi-n">{{ attenPct }}</div>
          <div class="kpi-l">峰值当日虚高衰减</div>
        </div>
        <div class="kpi kpi-amber">
          <div class="kpi-n">{{ recon.peaks.service_peak.count }}</div>
          <div class="kpi-l">真实服务峰（{{ recon.peaks.service_peak.date.slice(5) }}）</div>
        </div>
        <div class="kpi">
          <div class="kpi-n">{{ recon.totals.known_service_units }}</div>
          <div class="kpi-l">目标期可定时点总量</div>
        </div>
        <div class="kpi" :class="{ 'kpi-unknown': recon.totals.unknown_units }">
          <div class="kpi-n">{{ recon.totals.unknown_units }}</div>
          <div class="kpi-l">服务时间未知（不分配）</div>
        </div>
      </div>

      <!-- 主图 -->
      <div class="chart-box mt12" v-loading="loading">
        <ActivityCurveChart
          :labels="recon?.arrival_axis || []"
          :received="recon?.received_daily || []"
          :service="serviceAligned"
          :deconv="deconvAligned"
          :batch="batchAligned"
          :show-received="showReceived"
          :show-service="showService"
          :show-deconv="showDeconv"
          :show-batch="showBatch" />
      </div>
      <div class="legend-row">
        <span><i class="lg lg-bar"></i>按签收/接收日</span>
        <span><i class="lg lg-svc"></i>按服务时点（直接汇总/对账真值）</span>
        <span><i class="lg lg-deconv"></i>延迟反卷积估计</span>
        <span><i class="lg lg-band"></i>反卷积不确定带</span>
        <span><i class="lg lg-batch"></i>批次对账还原</span>
      </div>
      <div class="small muted mt8" v-if="recon?.coverage">
        覆盖区间：直接服务时间证据覆盖目标期
        <b>{{ recon.coverage.direct_evidenced_days }}/{{ recon.coverage.target_days }}</b> 天
        （{{ recon.coverage.evidenced_from }} ~ {{ recon.coverage.evidenced_to }}）；
        经验最大延迟 {{ recon.coverage.empirical_max_lag_days }} 天、到达轴至 {{ recon.coverage.arrival_horizon }}，
        故反卷积在 <b>{{ recon.coverage.deconv_observable_through }}</b> 前延迟到达可完整观测，
        右缘约 {{ recon.coverage.right_edge_possibly_censored_days }} 天可能删失（已体现在不确定区间）。
      </div>
      <el-alert class="mt8" type="info" :closable="false" v-if="recon">
        <template #title>
          <b>峰值如何改变：</b>
          原始接收日最高为 <b class="red">{{ recon.peaks.raw_peak.date }}</b>（{{ recon.peaks.raw_peak.count }} 笔），
          当天真实服务仅 <b>{{ recon.peaks.raw_peak_corrected_same_day }}</b> 笔；
          按服务时点重建后，最高峰移到
          <b class="blue">{{ recon.peaks.service_peak.date }}</b>（{{ recon.peaks.service_peak.count }} 笔）。
          <span v-if="recon.peaks.raw_peak_attenuation != null">
            峰值当日口径衰减 <b class="green">{{ attenPct }}</b>。
          </span>
        </template>
      </el-alert>
    </div>

    <!-- 峰值分离 -->
    <el-row :gutter="14" v-if="recon">
      <el-col :xs="24" :md="12">
        <div class="card peak-card">
          <h3>📦 传输批次峰（接收口径，应被解释/消除）</h3>
          <div v-for="p in recon.peaks.transmission_peaks" :key="p.date" class="peak-item batch">
            <div class="flex-between">
              <b>{{ p.date }}</b>
              <el-tag type="danger" size="small" effect="plain">签收 {{ p.received }} · 当日服务 {{ p.same_day_service }}</el-tag>
            </div>
            <p class="small muted" style="margin:6px 0 0;line-height:1.7">{{ p.text }}</p>
          </div>
          <el-empty v-if="!recon.peaks.transmission_peaks.length" description="当前范围无多日压批型接收峰" :image-size="60" />
        </div>
      </el-col>
      <el-col :xs="24" :md="12">
        <div class="card peak-card">
          <h3>🩺 真实服务峰（服务时点口径，需保留并核查）</h3>
          <div v-for="p in recon.peaks.real_peaks" :key="p.date" class="peak-item real">
            <div class="flex-between">
              <b>{{ p.date }}</b>
              <el-tag type="primary" size="small" effect="plain">服务 {{ p.service_units }} 笔</el-tag>
            </div>
            <p class="small muted" style="margin:6px 0 0;line-height:1.7">{{ p.text }}</p>
          </div>
          <el-empty v-if="!recon.peaks.real_peaks.length" description="当前范围无显著真实服务峰" :image-size="60" />
        </div>
      </el-col>
    </el-row>

    <!-- 两条方法 + 直接口径 -->
    <el-row :gutter="14" v-if="recon">
      <el-col :xs="24" :md="8">
        <div class="card method-card exact">
          <h3>① 服务时间直接汇总 <el-tag size="small" type="success">真值参照</el-tag></h3>
          <div class="method-total">{{ recon.totals.known_service_units }} <span>笔可定时点</span></div>
          <p class="small muted">对服务发生时间非空的记录按日直接归集；缺失的 {{ recon.unknown.units }} 笔保留未知、不进任何日期。</p>
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="card method-card" :class="recon.batch.available ? 'high' : 'disabled'">
          <h3>② 清算批次对账还原
            <el-tag size="small" :type="recon.batch.available ? 'success' : 'info'">
              {{ recon.batch.available ? '高精度' : '不可用' }}
            </el-tag>
          </h3>
          <template v-if="recon.batch.available && recon.batch.total">
            <div class="method-total">{{ recon.batch.total.estimate }}
              <span>笔 · 区间 [{{ recon.batch.total.low }}, {{ recon.batch.total.high }}]</span>
            </div>
            <p class="small muted">
              已对平 {{ recon.batch.total.reconciled_units }} 笔；未对平/缺清单
              {{ recon.batch.total.unreconciled_units }} 笔。覆盖机构
              {{ recon.batch.covered_orgs.join('、') || '—' }}<template v-if="recon.batch.excluded_orgs.length">；
              排除无批次号 {{ recon.batch.excluded_orgs.join('、') }}</template>。
            </p>
          </template>
          <el-alert v-else type="warning" :title="recon.batch.reason" :closable="false" class="small" />
        </div>
      </el-col>
      <el-col :xs="24" :md="8">
        <div class="card method-card deconv">
          <h3>③ 延迟分布反卷积
            <el-tag size="small" type="warning">估计+区间</el-tag>
          </h3>
          <div class="method-total">{{ recon.deconv.total.estimate }}
            <span>笔 · 区间 [{{ recon.deconv.total.low }}, {{ recon.deconv.total.high }}]</span>
          </div>
          <p class="small muted">分机构核 + Richardson–Lucy 反演，总质量守恒；区间表达计数波动与核误设。</p>
          <div v-for="o in recon.deconv.by_org" :key="o.org_code" class="org-rel">
            <el-tag size="small" :type="relType(o.reliability)">{{ relLabel(o.reliability) }}</el-tag>
            <b class="mono">{{ o.org_code }}</b>
            <span class="small muted">核 K={{ o.kernel.max_lag }} / 均迟 {{ o.kernel.mean_lag }}d / 回测MAE {{ o.retrospective_mae_daily }}</span>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 来源画像与质量反馈 -->
    <div class="card">
      <h3>🏥 来源画像 · 经验延迟分布 · 来源质量反馈</h3>
      <el-row :gutter="14">
        <el-col v-for="p in profiles" :key="p.code" :xs="24" :md="8">
          <div class="profile-card" :class="p.level">
            <div class="flex-between">
              <div>
                <b>{{ p.org_code }}</b>
                <span class="small muted"> {{ p.org_name.replace(/（.*?）/g, '') }}</span>
              </div>
              <el-tag :type="scoreType(p.level)" effect="dark" size="small">质量 {{ p.quality_score }}</el-tag>
            </div>
            <el-tag size="small" class="mt8" type="info" effect="plain">{{ p.report_mode_label }}</el-tag>
            <p class="small" style="line-height:1.7;margin:8px 0">{{ p.mechanism_desc }}</p>

            <div class="metric-row">
              <span>服务时间完整度 <b>{{ pct(p.metrics.service_time_completeness) }}</b></span>
              <span>批次号覆盖 <b>{{ pct(p.metrics.batch_no_coverage) }}</b></span>
              <span>平均延迟 <b>{{ p.metrics.mean_lag_days }} 天</b></span>
              <span>当日达 <b>{{ pct(p.metrics.same_day_ratio) }}</b></span>
              <span>最忙签收日占比 <b>{{ pct(p.metrics.peak_signoff_day_share) }}</b></span>
            </div>

            <div class="hist">
              <div v-for="h in p.delay_histogram" :key="h.lag_days" class="hist-col"
                   :title="`延迟 ${h.lag_days} 天：${h.units} 笔 (${pct(h.prob)})`">
                <div class="hist-bar" :style="{ height: histHeight(h.prob, p.delay_histogram) }"></div>
                <div class="hist-x">{{ h.lag_days }}</div>
              </div>
            </div>
            <div class="small muted" style="text-align:center">经验延迟（天）分布</div>

            <ul class="feedback">
              <li v-for="(f, i) in p.feedback" :key="i" :class="f.tone">{{ f.tone === 'good' ? '✓' : '!' }} {{ f.text }}</li>
            </ul>

            <el-alert v-if="!p.batch_correction.applicable" type="warning" :closable="false" class="mt8">
              <template #title>
                <b>为何不能套用 B 的批次校正：</b>{{ p.batch_correction.cross_org_guard?.reason || p.batch_correction.reason }}
              </template>
            </el-alert>
            <div v-else class="small batch-ok mt8">✓ {{ p.batch_correction.reason }}</div>
          </div>
        </el-col>
      </el-row>
    </div>

    <!-- 明细 / 批次 / 每日 -->
    <div class="card">
      <el-tabs v-model="tab">
        <el-tab-pane label="每日重建数据" name="days">
          <el-table :data="dayRows" size="small" border max-height="460">
            <el-table-column prop="date" label="日期(服务时点)" width="130" class-name="mono" />
            <el-table-column label="签收/接收日量" width="130" align="right">
              <template #default="{ row }">{{ row.received }}</template>
            </el-table-column>
            <el-table-column label="服务时点(直接)" width="140" align="right">
              <template #default="{ row }"><b class="blue">{{ row.direct }}</b></template>
            </el-table-column>
            <el-table-column label="反卷积估计 [低,高]" min-width="180">
              <template #default="{ row }">
                <span v-if="row.deconv" class="mono small">
                  {{ row.deconv.e }} <span class="muted">[{{ row.deconv.lo }}, {{ row.deconv.hi }}]</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="批次对账" width="110" align="right">
              <template #default="{ row }">{{ row.batch ?? '—' }}</template>
            </el-table-column>
          </el-table>
          <p class="small muted mt8">
            注：签收/接收量按 arrival_axis 对齐到同一日历日；4/1–4/2 的 C 延迟签收仍在到达轴内，但不属于 3 月服务期。
          </p>
        </el-tab-pane>

        <el-tab-pane :label="`清算批次清单 (${batches.length})`" name="batches">
          <el-table :data="batches" size="small" border max-height="460">
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="manifest">
                  <div v-for="(v, d) in row.service_dates" :key="d" class="manifest-day mono">
                    {{ d }}：就诊 {{ v.visit }} / 结算 {{ v.settlement }}
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="batch_no" label="批次号" width="170" class-name="mono" />
            <el-table-column prop="org_code" label="机构" width="90" />
            <el-table-column label="签收时间" width="165">
              <template #default="{ row }">{{ fmt(row.signed_at) }}</template>
            </el-table-column>
            <el-table-column prop="service_date_count" label="服务日数" width="90" align="center" />
            <el-table-column prop="seq_count" label="批次内序号数" width="110" align="center" />
            <el-table-column prop="declared_units" label="清单声明笔数" width="110" align="center" />
            <el-table-column label="元数据" width="110" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.metadata_complete ? 'success' : 'warning'">
                  {{ row.metadata_complete ? '完整可对账' : '不完整' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" min-width="200" show-overflow-tooltip />
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="来源明细（五要素）" name="records">
          <div class="flex gap8 mb8" style="align-items:center">
            <el-switch v-model="unknownOnly" @change="loadRecords" />
            <span class="small">只看服务时间缺失（未知）记录</span>
          </div>
          <el-table :data="records" size="small" border max-height="460">
            <el-table-column prop="code" label="明细编号" width="170" class-name="mono" />
            <el-table-column prop="org_code" label="机构" width="80" />
            <el-table-column prop="kind" label="类型" width="80">
              <template #default="{ row }">{{ row.kind === 'visit' ? '就诊' : '结算' }}</template>
            </el-table-column>
            <el-table-column prop="batch_no" label="清算批次" width="150" class-name="mono">
              <template #default="{ row }">{{ row.batch_no || '—（无批次号）' }}</template>
            </el-table-column>
            <el-table-column prop="batch_seq" label="序号" width="64" align="center">
              <template #default="{ row }">{{ row.batch_seq ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="服务发生时间" width="140">
              <template #default="{ row }">
                <el-tag v-if="!row.service_date" type="danger" size="small" effect="dark">未知·不分配</el-tag>
                <span v-else class="mono">{{ row.service_date }}</span>
              </template>
            </el-table-column>
            <el-table-column label="签收时间" width="160">
              <template #default="{ row }">{{ fmt(row.signed_at) }}</template>
            </el-table-column>
            <el-table-column prop="unit_count" label="笔数" width="70" align="center" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import api from '../api.js'
import ActivityCurveChart from '../components/ActivityCurveChart.vue'

const profiles = ref([])
const recon = ref(null)
const batches = ref([])
const records = ref([])
const org = ref('ALL')
const kind = ref('visit')
const tab = ref('days')
const unknownOnly = ref(false)
const loading = ref(false)
const showReceived = ref(true)
const showService = ref(true)
const showDeconv = ref(false)
const showBatch = ref(false)

function fmt(s) { return s ? s.replace('T', ' ').slice(0, 16) : '' }
function pct(x) { return `${Math.round((x ?? 0) * 100)}%` }
function scoreType(lv) { return lv === 'good' ? 'success' : lv === 'warn' ? 'warning' : 'danger' }
function relType(r) { return r === 'high' ? 'success' : r === 'medium' ? 'warning' : 'danger' }
function relLabel(r) { return { high: '高可信', medium: '中等', low: '机制误设·低可信' }[r] || r }
function histHeight(prob, hist) {
  const max = Math.max(...hist.map(h => h.prob), 0.0001)
  return `${Math.max(6, (prob / max) * 56)}px`
}

const attenPct = computed(() => {
  const a = recon.value?.peaks?.raw_peak_attenuation
  return a == null ? '—' : `${Math.round(a * 100)}%`
})

const axisIndex = computed(() => {
  const m = {}
  ;(recon.value?.arrival_axis || []).forEach((d, i) => { m[d] = i })
  return m
})

const serviceAligned = computed(() => {
  const r = recon.value
  if (!r) return []
  const out = new Array(r.arrival_axis.length).fill(null)
  r.service_axis.forEach((d, j) => {
    const i = axisIndex.value[d]
    if (i != null) out[i] = r.direct_daily[j]
  })
  return out
})

const deconvAligned = computed(() => {
  const r = recon.value
  if (!r) return []
  const out = new Array(r.arrival_axis.length).fill(null)
  r.deconv.daily.forEach(x => {
    const i = axisIndex.value[x.date]
    if (i != null) out[i] = { e: x.estimate, lo: x.low, hi: x.high }
  })
  return out
})

const batchAligned = computed(() => {
  const r = recon.value
  if (!r || !r.batch.available) return new Array((r?.arrival_axis || []).length).fill(null)
  const out = new Array(r.arrival_axis.length).fill(null)
  r.batch.daily.forEach(x => {
    const i = axisIndex.value[x.date]
    if (i != null) out[i] = x.estimate
  })
  return out
})

const dayRows = computed(() => {
  const r = recon.value
  if (!r) return []
  const recvMap = {}
  r.arrival_axis.forEach((d, i) => { recvMap[d] = r.received_daily[i] })
  const deconvMap = Object.fromEntries(r.deconv.daily.map(x => [x.date, x]))
  const batchMap = r.batch.available ? Object.fromEntries(r.batch.daily.map(x => [x.date, x.estimate])) : {}
  return r.service_axis.map((d, j) => ({
    date: d,
    received: recvMap[d] ?? 0,
    direct: r.direct_daily[j],
    deconv: deconvMap[d] ? { e: deconvMap[d].estimate, lo: deconvMap[d].low, hi: deconvMap[d].high } : null,
    batch: d in batchMap ? batchMap[d] : null
  }))
})

async function loadProfiles() {
  profiles.value = (await api.get('/api/activity/profiles')).items
}
async function loadRecon() {
  loading.value = true
  try {
    recon.value = await api.get('/api/activity/reconstruct', {
      params: { org: org.value, kind: kind.value }
    })
    const b = await api.get('/api/activity/batches', {
      params: org.value === 'ALL' ? {} : { org: org.value }
    })
    batches.value = b.items
    loadRecords()
  } finally {
    loading.value = false
  }
}
async function loadRecords() {
  const r = await api.get('/api/activity/records', {
    params: {
      org: org.value === 'ALL' ? undefined : org.value,
      kind: kind.value,
      unknown_only: unknownOnly.value || undefined,
      limit: 600
    }
  })
  records.value = r.items
}

onMounted(async () => {
  await loadProfiles()
  await loadRecon()
})
</script>

<style scoped>
.acc-page :deep(.el-checkbox__label) { font-size: 13px; }
.kpi-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-top: 14px; }
.kpi { background: #f8fafc; border: 1px solid var(--rg-line, #e5eaf3); border-radius: 10px; padding: 12px 14px; }
.kpi-n { font-size: 26px; font-weight: 800; line-height: 1.1; color: #0f172a; }
.kpi-l { font-size: 12px; color: #64748b; margin-top: 4px; line-height: 1.4; }
.kpi-red .kpi-n { color: #dc2626; }
.kpi-blue .kpi-n { color: #1d4ed8; }
.kpi-green .kpi-n { color: #15803d; }
.kpi-amber .kpi-n { color: #b45309; }
.kpi-unknown { background: #fef2f2; border-color: #fecaca; }
.kpi-unknown .kpi-n { color: #dc2626; }
@media (max-width: 1100px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); } }

.chart-box { width: 100%; }
.legend-row { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: #475569; margin-top: 6px; }
.legend-row span { display: inline-flex; align-items: center; gap: 6px; }
.lg { width: 16px; height: 10px; border-radius: 2px; display: inline-block; }
.lg-bar { background: rgba(220,38,38,.42); }
.lg-svc { background: #1d4ed8; height: 3px; width: 20px; border-radius: 2px; }
.lg-deconv { background: repeating-linear-gradient(90deg,#7c3aed 0 3px,transparent 3px 5px); height: 3px; }
.lg-band { background: rgba(124,58,237,.18); border: 1px solid rgba(124,58,237,.35); }
.lg-batch { background: #0f766e; height: 3px; width: 20px; }
.red { color: #dc2626; } .blue { color: #1d4ed8; } .green { color: #15803d; }

.peak-card h3, .method-card h3 { font-size: 15px; margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
.peak-item { border-left: 3px solid #cbd5e1; padding: 8px 12px; margin-bottom: 10px; background: #f8fafc; border-radius: 0 8px 8px 0; }
.peak-item.batch { border-left-color: #dc2626; background: #fef6f6; }
.peak-item.real { border-left-color: #1d4ed8; background: #f5f8ff; }

.method-card { min-height: 172px; }
.method-card.high { border-color: #99f6e4; }
.method-card.deconv { border-color: #ddd6fe; }
.method-card.disabled { background: #f8fafc; }
.method-total { font-size: 26px; font-weight: 800; color: #0f766e; margin: 4px 0 6px; }
.method-card.deconv .method-total { color: #6d28d9; }
.method-total span { font-size: 12.5px; font-weight: 500; color: #64748b; }
.org-rel { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }

.profile-card { border: 1px solid var(--rg-line, #e5eaf3); border-radius: 12px; padding: 14px; margin-bottom: 14px; background: #fff; height: calc(100% - 14px); }
.profile-card.good { border-top: 3px solid #16a34a; }
.profile-card.warn { border-top: 3px solid #d97706; }
.profile-card.bad { border-top: 3px solid #dc2626; }
.metric-row { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; font-size: 12.5px; color: #64748b; margin: 8px 0; }
.metric-row b { color: #0f172a; float: right; }
.hist { display: flex; align-items: flex-end; gap: 3px; height: 64px; padding: 6px 4px 0; border-bottom: 1px solid #eef2f7; }
.hist-col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; }
.hist-bar { width: 70%; max-width: 22px; background: linear-gradient(180deg,#60a5fa,#1d4ed8); border-radius: 3px 3px 0 0; }
.hist-x { font-size: 10px; color: #94a3b8; margin-top: 2px; }
.feedback { list-style: none; padding: 0; margin: 10px 0 0; }
.feedback li { font-size: 12.5px; line-height: 1.6; padding: 4px 0 4px 22px; position: relative; color: #475569; }
.feedback li::before { position: absolute; left: 0; top: 4px; font-weight: 800; }
.feedback li.good { color: #15803d; } .feedback li.good::before { content: '✓'; }
.feedback li.warn { color: #b45309; } .feedback li.warn::before { content: '!'; }
.feedback li.bad { color: #b91c1c; } .feedback li.bad::before { content: '×'; }
.batch-ok { color: #0f766e; }
.manifest { padding: 8px 16px; display: flex; flex-wrap: wrap; gap: 6px 18px; background: #f8fafc; }
.manifest-day { font-size: 12.5px; color: #334155; }
.mb8 { margin-bottom: 8px; }
</style>
