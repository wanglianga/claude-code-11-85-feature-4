"""服务活动时间重建。

背景：结算平台常在月末/周一批量补传。若按平台签收（接收）日统计，原本按服务
时点分散的诊疗会在批次签收日叠加成人为高峰，并可能触发错误线索。

本模块提供两条相互独立、估计同一目标期间服务发生量的路径：

1. ``delay_deconv``：按机构从“服务发生时间—签收时间”样本估计经验延迟分布 p(d)，
   建立卷积模型  R_j = Σ_d p_d · S_{j-d}，用 Richardson–Lucy（泊松 EM）反卷积
   还原日级服务量。它能表达“平滑迟到”，但前提是延迟机制稳定；对 B 这种
   “按星期几确定性压批”的机构，混合延迟核存在模型误设，只给近似 + 更宽区间。

2. ``batch_reconcile``：用清算批次清单（manifest 中的服务日/笔数 + 序号连续性）
   与明细对账，直接还原批次覆盖的服务日。元数据完整时高精度（本样例中为精确），
   但没有批次号（C）或批次元数据不完整时不可用。

铁律：``service_date IS NULL`` 的记录只计入 unknown 桶及其不确定区间，
绝不分配到月末或任何具体日期。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import ActivityRecord, ClearingBatch, SourceProfile

# 默认目标期间：2026 年 3 月（月末批量补传场景）
TARGET_START = date(2026, 3, 1)
TARGET_END = date(2026, 3, 31)

KIND_LABEL = {"visit": "就诊", "settlement": "结算"}


# ---------------------------------------------------------------- 基础工具
def daterange(start: date, end: date) -> list[date]:
    n = (end - start).days + 1
    return [start + timedelta(days=i) for i in range(n)]


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def _weighted_quantile(hist: dict[int, float], q: float) -> float:
    total = sum(hist.values())
    if total <= 0:
        return 0.0
    acc = 0.0
    target = q * total
    for lag in sorted(hist):
        acc += hist[lag]
        if acc >= target:
            return float(lag)
    return float(max(hist))


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- 数据装载
def load_profiles(db: Session) -> list[SourceProfile]:
    return db.query(SourceProfile).order_by(SourceProfile.org_code).all()


def load_rows(db: Session, org_code: str | None = None,
              kind: str = "visit") -> list[ActivityRecord]:
    q = db.query(ActivityRecord).filter(ActivityRecord.kind == kind)
    if org_code and org_code != "ALL":
        q = q.filter(ActivityRecord.org_code == org_code)
    return q.order_by(ActivityRecord.id).all()


def load_batches(db: Session, org_code: str | None = None) -> list[ClearingBatch]:
    q = db.query(ClearingBatch)
    if org_code and org_code != "ALL":
        q = q.filter(ClearingBatch.org_code == org_code)
    return q.order_by(ClearingBatch.signed_at, ClearingBatch.batch_no).all()


# ---------------------------------------------------------------- 延迟模型
def delay_kernel(rows: list[ActivityRecord]) -> dict:
    """估计经验到达延迟（签收日 − 服务日，天）分布，按 unit_count 加权。"""
    hist: dict[int, float] = {}
    weighted: list[int] = []
    samples = 0
    for r in rows:
        if r.service_date is None:
            continue
        lag = (r.signed_at.date() - r.service_date).days
        if lag < 0:  # 时区/录入抖动保护
            lag = 0
        w = int(r.unit_count or 1)
        hist[lag] = hist.get(lag, 0.0) + w
        samples += w
        weighted.extend([lag] * min(w, 50))  # 分位数用，避免超大列表

    if samples == 0:
        return {"histogram": [], "probs": [1.0], "lags": [0], "max_lag": 0,
                "mean_lag": 0.0, "median_lag": 0.0, "p90_lag": 0.0,
                "same_day_ratio": 1.0, "samples": 0}

    k = max(hist)
    # 轻量加性平滑，稳定零频格子（不向观测最大延迟之外外推支撑）
    alpha = 0.5
    probs = []
    for lag in range(k + 1):
        probs.append(hist.get(lag, 0.0) + alpha)
    z = sum(probs)
    probs = [p / z for p in probs]

    mean_lag = sum(lag * hist.get(lag, 0.0) for lag in range(k + 1)) / samples
    hist_rows = [
        {"lag_days": lag, "units": int(hist.get(lag, 0)),
         "prob": round(hist.get(lag, 0.0) / samples, 4)}
        for lag in range(k + 1)
    ]
    return {
        "histogram": hist_rows, "probs": probs, "lags": list(range(k + 1)),
        "max_lag": k, "mean_lag": round(mean_lag, 2),
        "median_lag": _weighted_quantile(hist, 0.5),
        "p90_lag": _weighted_quantile(hist, 0.9),
        "same_day_ratio": round(hist.get(0, 0.0) / samples, 3),
        "samples": samples,
    }


# ---------------------------------------------------------------- 序列统计
def _received_by_signoff(rows: list[ActivityRecord], axis: list[date]) -> list[float]:
    idx = {d: i for i, d in enumerate(axis)}
    out = [0.0] * len(axis)
    for r in rows:
        d = r.signed_at.date()
        if d in idx:
            out[idx[d]] += int(r.unit_count or 1)
    return out


def _service_by_date(rows: list[ActivityRecord], dates: list[date]) -> list[float]:
    idx = {d: i for i, d in enumerate(dates)}
    out = [0.0] * len(dates)
    for r in rows:
        if r.service_date is not None and r.service_date in idx:
            out[idx[r.service_date]] += int(r.unit_count or 1)
    return out


def _unknown(rows: list[ActivityRecord]) -> dict:
    by_sign: dict[str, int] = {}
    units = 0
    codes = []
    for r in rows:
        if r.service_date is None:
            w = int(r.unit_count or 1)
            units += w
            key = r.signed_at.date().isoformat()
            by_sign[key] = by_sign.get(key, 0) + w
            codes.append(r.code)
    return {"units": units, "by_signoff": by_sign, "record_codes": codes}


# ---------------------------------------------------------------- 反卷积
def _richardson_lucy(received: list[float], kernel: list[float],
                     n_target: int, iters: int = 120) -> list[float]:
    """泊松 EM / Richardson–Lucy 卷积反演。

    模型 R[j] = Σ_d kernel[d] · S[j-d]，j 覆盖 0..n_target-1+len(kernel)-1，
    因此每个目标日的全部核偏移都在观测轴内，列和恰为 1，总质量守恒。
    """
    k = len(kernel) - 1
    m = n_target + k
    r = list(received) + [0.0] * max(0, m - len(received))
    r = r[:m]
    total = sum(r)
    if total <= 0:
        return [0.0] * n_target
    x = [total / n_target] * n_target
    for _ in range(iters):
        fwd = [0.0] * m
        for i in range(n_target):
            xi = x[i]
            if xi <= 1e-12:
                continue
            for d, pd in enumerate(kernel):
                if pd:
                    fwd[i + d] += xi * pd
        for i in range(n_target):
            corr = 0.0
            for d, pd in enumerate(kernel):
                if not pd:
                    continue
                fj = fwd[i + d]
                if fj > 1e-12:
                    corr += pd * r[i + d] / fj
            x[i] = max(1e-9, x[i] * corr)
    return x


def deconv_for_scope(rows: list[ActivityRecord], dates: list[date],
                     scheduled_orgs: set[str] | None = None) -> dict:
    """按机构分别估计经验延迟核并反卷积，再把同日结果相加。

    不能把 A/B/C 混成一个池化核：三家延迟机制不同，池化核谁都代表不了。
    未知服务时间记录不参与反演，只计入 unknown 与总量上限。
    """
    scheduled_orgs = scheduled_orgs or set()
    n = len(dates)
    unknown = _unknown(rows)
    agg_est = [0.0] * n
    agg_low = [0.0] * n
    agg_high = [0.0] * n
    by_org = []
    known_total = 0

    for org in sorted({r.org_code for r in rows}):
        orows = [r for r in rows if r.org_code == org]
        kernel = delay_kernel(orows)
        if kernel["samples"] == 0:
            continue
        probs = kernel["probs"]
        k = kernel["max_lag"]
        known = [r for r in orows if r.service_date is not None]
        known_total += sum(int(r.unit_count or 1) for r in known)

        obs_dates = daterange(dates[0], dates[-1] + timedelta(days=k))
        r_known = _received_by_signoff(known, obs_dates)
        # 25 次迭代做适度正则，避免 RL 在误设核上过拟合出尖峰
        x = _richardson_lucy(r_known, probs, n, iters=25)
        truth = _service_by_date(orows, dates)

        scheduled = org in scheduled_orgs
        # 系统误差比例：基础项 + 延迟支撑宽度 + “固定压批日历导致混合核误设”
        alpha = 0.10 + 0.03 * k + (0.22 if scheduled else 0.0)
        o_low = o_high = 0.0
        abs_err = 0.0
        for i in range(n):
            c = max(0.0, x[i])
            margin = math.sqrt(max(c, 1.0)) + alpha * c
            lo = max(0.0, c - margin)
            hi = c + margin
            agg_est[i] += c
            agg_low[i] += lo
            agg_high[i] += hi
            o_low += lo
            o_high += hi
            abs_err += abs(c - truth[i])

        if scheduled:
            reliability, warning = "low", (
                "延迟由固定压批日历（按星期几）生成，单一混合延迟核不满足“平滑 iid 迟到”"
                "假设，反卷积仅粗略且区间宽；该机构批次清单完整，应改用批次对账精确还原。")
        elif k <= 2 and kernel["samples"] >= 80:
            reliability, warning = "high", None
        else:
            reliability, warning = "medium", "样本或延迟支撑一般，反卷积结果需结合不确定区间解读。"

        by_org.append({
            "org_code": org, "reliability": reliability,
            "mechanism_warning": warning,
            "kernel": {kk: vv for kk, vv in kernel.items() if kk != "probs"},
            "total_estimate": round(sum(x), 1),
            "total_low": round(o_low, 1), "total_high": round(o_high, 1),
            # 样例保留了服务时间真值，可回测反卷积对这条历史窗口的拟合误差
            "retrospective_mae_daily": round(abs_err / n, 2),
        })

    daily = []
    lows, highs = [], []
    for i, d in enumerate(dates):
        est = round(agg_est[i], 1)
        lo = min(est, round(agg_low[i], 1))
        hi = max(est, round(agg_high[i], 1))
        lows.append(lo)
        highs.append(hi)
        daily.append({"date": d.isoformat(), "estimate": est,
                      "low": lo, "high": hi})

    est_total = round(sum(agg_est), 1)
    low_total = min(est_total, round(sum(agg_low), 1))
    # 未知服务时间可能落在目标期间，计入上限而不摊到任何一天
    high_total = max(est_total, round(sum(agg_high) + unknown["units"], 1))

    return {
        "method": "delay_deconv",
        "label": "分机构经验延迟分布 · 反卷积",
        "available": bool(by_org),
        "precision": "estimate_with_range",
        "by_org": by_org,
        "daily": daily,
        "total": {"estimate": est_total, "low": low_total, "high": high_total,
                  "known_service_units": known_total,
                  "unknown_units": unknown["units"]},
        "unknown": unknown,
        "notes": [
            "按机构建立卷积 R=S∗p，Richardson–Lucy 反演，再跨机构相加。",
            "适合平滑/分散且延迟机制稳定的来源（如 C）；固定压批日历机构（B）存在核误设。",
            "区间含计数波动与模型误差；服务时间缺失量只计入总量上限，不分配到日。",
        ],
    }


# ---------------------------------------------------------------- 批次对账
BATCH_UNAVAILABLE = {
    "no_batch_no": "该来源没有清算批次号，无法按批次清单对账还原；也不能借用其他机构的批次日历。",
    "metadata_incomplete": "清算批次元数据不完整（缺服务日清单/序号不连续/数对不平），不能高精度还原。",
    "no_complete_batch": "没有可对账的完整清算批次。",
}


def batch_applicability(profile: SourceProfile | None,
                        rows: list[ActivityRecord]) -> tuple[bool, str]:
    batch_rows = [r for r in rows if r.batch_no]
    if not batch_rows or (profile is not None and not profile.has_batch_no):
        return False, "no_batch_no"
    if profile is not None and not profile.batch_metadata_complete:
        # 画像级标记仅作参考；最终以批次逐项对账为准
        pass
    return True, "ok"


def reconcile_batches(db: Session, rows: list[ActivityRecord],
                      dates: list[date], kind: str,
                      org_code: str = "ALL") -> dict:
    """用清算批次清单对账并直接还原服务日。仅完整且对平的批次产出日曲线。"""
    idx = {d: i for i, d in enumerate(dates)}
    daily_val = [0.0] * len(dates)
    batch_nos = sorted({r.batch_no for r in rows if r.batch_no})

    if not batch_nos:
        return {"method": "batch_reconcile",
                "label": "清算批次清单 · 对账还原", "available": False,
                "reason_code": "no_batch_no",
                "reason": BATCH_UNAVAILABLE["no_batch_no"], "daily": [],
                "total": None, "batches": [], "covered_orgs": [],
                "excluded_orgs": sorted({r.org_code for r in rows}),
                "precision": "high"}

    batches = db.query(ClearingBatch).filter(
        ClearingBatch.batch_no.in_(batch_nos)).all()
    bmap = {b.batch_no: b for b in batches}
    # 序号在“整批全部明细（就诊+结算）”内连续，需取全 kind 行校验
    all_kind_rows = db.query(ActivityRecord).filter(
        ActivityRecord.batch_no.in_(batch_nos)).all()

    per_batch = []
    reconciled_units = 0
    batch_scope_total = sum(int(r.unit_count or 1) for r in rows if r.batch_no)
    covered_orgs: set[str] = set()
    all_orgs = sorted({r.org_code for r in rows})

    for bno in batch_nos:
        b = bmap.get(bno)
        brows = [r for r in rows if r.batch_no == bno and r.kind == kind]
        actual_units = sum(int(r.unit_count or 1) for r in brows)
        seq_values = sorted(r.batch_seq for r in all_kind_rows
                            if r.batch_no == bno and r.batch_seq is not None)
        seq_contiguous = bool(seq_values) and seq_values == list(
            range(seq_values[0], seq_values[-1] + 1)) \
            and len(set(seq_values)) == len(seq_values)

        manifest = (b.service_dates if b else {}) or {}
        manifest_units = sum(int(v.get(kind, 0)) for v in manifest.values())
        complete = bool(b and b.metadata_complete and manifest
                        and seq_contiguous and manifest_units == actual_units)

        item = {
            "batch_no": bno, "org_code": b.org_code if b else brows[0].org_code,
            "signed_at": b.signed_at.isoformat() if b and b.signed_at else
            (brows[0].signed_at.isoformat() if brows else None),
            "actual_units": actual_units, "manifest_units": manifest_units,
            "seq_contiguous": seq_contiguous,
            "metadata_complete": bool(b and b.metadata_complete),
            "reconciled": complete,
            "service_dates": sorted(manifest.keys()),
        }
        if complete:
            for diso, kv in manifest.items():
                d = date.fromisoformat(diso)
                if d in idx:
                    daily_val[idx[d]] += int(kv.get(kind, 0))
            reconciled_units += actual_units
            covered_orgs.add(item["org_code"])
        else:
            item["reconciled"] = False
        per_batch.append(item)

    available = reconciled_units > 0
    if not available:
        reason_code = "metadata_incomplete" if batches else "no_complete_batch"
        return {"method": "batch_reconcile",
                "label": "清算批次清单 · 对账还原", "available": False,
                "reason_code": reason_code,
                "reason": BATCH_UNAVAILABLE.get(reason_code, ""), "daily": [],
                "total": None, "batches": per_batch, "covered_orgs": [],
                "excluded_orgs": all_orgs, "precision": "high"}

    daily = [{"date": d.isoformat(), "estimate": round(daily_val[i], 1),
              "low": round(daily_val[i], 1), "high": round(daily_val[i], 1)}
             for i, d in enumerate(dates)]
    est = round(sum(daily_val), 1)
    unknown = _unknown(rows)
    # 区间下界=已对平还原；上界=所有“带批次号”的本类活动（含对不平/缺服务日者）。
    # 无批次号的机构（C）整体不属于本方法，计入 excluded_orgs 而非此区间。
    return {
        "method": "batch_reconcile",
        "label": "清算批次清单 · 对账还原", "available": True,
        "reason_code": "ok", "precision": "high",
        "daily": daily,
        "total": {"estimate": est, "low": est,
                  "high": float(batch_scope_total),
                  "reconciled_units": reconciled_units,
                  "unreconciled_units": batch_scope_total - reconciled_units,
                  "unknown_units": unknown["units"]},
        "batches": per_batch,
        "covered_orgs": sorted(covered_orgs),
        "excluded_orgs": [o for o in all_orgs if o not in covered_orgs],
        "notes": [
            "批次号 + 签收时间 + 批次内序号连续性 + 清单服务日笔数四方对账。",
            "对平后按清单直接还原服务日，元数据完整时为精确值，区间最窄。",
            "无批次号或元数据不完整的来源/批次不参与，其数量计入总量上限。",
        ],
    }


def why_cannot_transfer_batch(source_org: str, target_profile: SourceProfile) -> dict:
    """明确禁止把 B 的批次校正（周一/四天日历）套用到 C。"""
    if target_profile.has_batch_no:
        return {"allowed": False, "reason_code": "org_specific_calendar",
                "reason": "批次—服务日映射是机构特定的，仍不可跨机构照搬。"}
    return {
        "allowed": False,
        "reason_code": "target_has_no_batch_no",
        "source_org": source_org,
        "target_org": target_profile.org_code,
        "reason": (
            f"{target_profile.org_code} 没有清算批次号，明细无法与 "
            f"{source_org} 的批次清单连接（join 键缺失）；且“周末四天→周一”"
            "是 B 机构的压批日历，并非通用延迟规律。套用会把 B 的服务日结构"
            "虚构到 C 上。C 服务时间可靠，应直接按服务时点汇总或用 C 自身"
            "经验延迟核反卷积。"
        ),
    }


# ---------------------------------------------------------------- 峰值分离
def _top_days(values: list[float], labels: list[str], k: int = 4) -> list[dict]:
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=True)[:k]
    return [{"date": labels[i], "count": int(round(values[i]))} for i in order
            if values[i] > 0]


def interpret_peaks(rows: list[ActivityRecord], dates: list[date],
                    axis: list[date], received_all: list[float],
                    direct: list[float], db: Session, kind: str) -> dict:
    """把“传输批次峰值”和“真实服务峰值”分开，并给出来源层面的解释。"""
    date_index = {d: i for i, d in enumerate(dates)}
    axis_index = {d: i for i, d in enumerate(axis)}

    raw_peak_i = max(range(len(axis)), key=lambda i: received_all[i])
    raw_peak = {"date": axis[raw_peak_i].isoformat(),
                "count": int(received_all[raw_peak_i])}
    svc_peak_i = max(range(len(dates)), key=lambda i: direct[i])
    service_peak = {"date": dates[svc_peak_i].isoformat(),
                    "count": int(direct[svc_peak_i])}

    svc_median = _median([v for v in direct if v > 0] or [0])

    # 原始接收日异常 -> 判断是否批次驱动。
    # 不用“接收日中位数倍数”做门槛：压批机构每个有量的接收日本来就是批次峰，
    # 中位数会被抬高形成循环判据。改用结构性判据：多日批次贡献占比 + 相对当日
    # 真实服务量的膨胀倍数。
    batch_cache = {b.batch_no: b for b in db.query(ClearingBatch).all()}
    transmission_peaks = []
    for i, v in enumerate(received_all):
        if v < 20:
            continue
        day = axis[i]
        day_rows = [r for r in rows if r.signed_at.date() == day]
        batch_driven = 0
        batch_nos = set()
        covered_ranges = []
        for r in day_rows:
            if not r.batch_no:
                continue
            b = batch_cache.get(r.batch_no)
            md = (b.service_dates if b else {}) or {}
            if len(md) >= 2:  # 多服务日压批才算“批次峰值”
                batch_driven += int(r.unit_count or 1)
                batch_nos.add(r.batch_no)
                ds = sorted(md.keys())
                if ds:
                    covered_ranges.append(f"{ds[0]}~{ds[-1]}")
        same_day_svc = direct[date_index[day]] if day in date_index else None
        share = batch_driven / v if v else 0
        if share >= 0.5 and (same_day_svc is None or v >= 1.4 * same_day_svc):
            transmission_peaks.append({
                "date": day.isoformat(), "received": int(v),
                "same_day_service": int(same_day_svc) if same_day_svc is not None else None,
                "batch_driven_units": int(batch_driven),
                "batch_driven_share": round(share, 2),
                "batch_nos": sorted(batch_nos),
                "covers_service_ranges": sorted(set(covered_ranges)),
                "verdict": "transmission_batch_peak",
                "text": (f"{day.isoformat()} 签收 {int(v)} 笔是清算批次压出来的接收峰："
                         f"{int(batch_driven)} 笔来自多日合并批次 {sorted(batch_nos)}，"
                         f"对应服务日 {sorted(set(covered_ranges))}，并非当天真实活动。"),
            })

    transmission_peaks.sort(key=lambda p: p["received"], reverse=True)

    # 真实服务日异常
    real_peaks = []
    # 服务日 -> 承载其签收的批次日（用于“真峰被批次峰掩盖”的解释）
    for i, v in enumerate(direct):
        if v < max(18, 1.6 * svc_median):
            continue
        d = dates[i]
        via = {}
        for r in rows:
            if r.service_date != d:
                continue
            key = r.batch_no or ("direct:" + r.signed_at.date().isoformat())
            via[key] = r.signed_at.date().isoformat()
        sign_days = sorted({sd for sd in via.values()})
        bundled = any(r.batch_no for r in rows if r.service_date == d)
        real_peaks.append({
            "date": d.isoformat(), "service_units": int(v),
            "arrival_days": sign_days, "bundled_in_batch": bundled,
            "verdict": "real_service_peak",
            "text": (f"{d.isoformat()} 按服务时点有 {int(v)} 笔，是真实服务高峰；"
                     + (f"这些活动被压到 {sign_days} 签收，在接收日曲线上被并入批次峰、掩盖了真实时点。"
                        if bundled else "按服务时点可直接观察。")),
        })

    raw_d = date.fromisoformat(raw_peak["date"])
    corrected_direct = direct[date_index[raw_d]] if raw_d in date_index else None

    attenuation = None
    if corrected_direct is not None and raw_peak["count"] > 0:
        attenuation = round(1 - corrected_direct / raw_peak["count"], 3)

    return {
        "raw_peak": raw_peak,
        "service_peak": service_peak,
        "raw_peak_corrected_same_day": int(corrected_direct) if corrected_direct is not None else None,
        "raw_peak_attenuation": attenuation,
        "peak_shift": {"from": raw_peak["date"], "to": service_peak["date"]},
        "transmission_peaks": transmission_peaks[:6],
        "real_peaks": real_peaks[:6],
    }


# ---------------------------------------------------------------- 来源质量反馈
def profile_feedback(db: Session) -> list[dict]:
    cards = []
    for p in load_profiles(db):
        rows = db.query(ActivityRecord).filter_by(org_code=p.org_code).all()
        total = sum(int(r.unit_count or 1) for r in rows)
        unknown = sum(int(r.unit_count or 1) for r in rows if r.service_date is None)
        known = total - unknown
        with_batch = sum(int(r.unit_count or 1) for r in rows if r.batch_no)
        kernel = delay_kernel([r for r in rows if r.kind == "visit"])

        # 签收日集中度
        sign_hist: dict[str, int] = {}
        for r in rows:
            k = r.signed_at.date().isoformat()
            sign_hist[k] = sign_hist.get(k, 0) + int(r.unit_count or 1)
        peak_day_share = round(max(sign_hist.values()) / total, 3) if total else 0

        svc_comp = round(known / total, 3) if total else 1.0
        batch_cov = round(with_batch / total, 3) if total else 0.0
        applicable, reason_code = batch_applicability(p, rows)
        guard = None
        if not applicable and reason_code == "no_batch_no":
            guard = why_cannot_transfer_batch("H-B", p)

        items = []
        score = 100
        if unknown:
            score -= min(18, round(100 * unknown / max(total, 1)) * 3 + 4)
            items.append({"tone": "warn", "code": "missing_service_time",
                          "text": f"{unknown} 笔缺少服务发生时间，已保留为未知、未分配到月末；建议补采服务时点。"})
        else:
            items.append({"tone": "good", "code": "service_time_complete",
                          "text": "服务发生时间齐全，可直接按服务时点重建。"})

        if p.has_batch_no and p.batch_metadata_complete:
            items.append({"tone": "good", "code": "batch_complete",
                          "text": "清算批次号、签收时间与批次清单完整，可高精度对账还原。"})
        elif not p.has_batch_no:
            score -= 8
            items.append({"tone": "warn", "code": "no_batch_no",
                          "text": "无清算批次号，不能走批次清单对账，也不能套用其他机构的批次校正；建议补传批次元数据。"})
        if kernel["mean_lag"] >= 2.5:
            score -= 10
            items.append({"tone": "warn", "code": "long_delay",
                          "text": f"平均到达延迟 {kernel['mean_lag']} 天、P90 {kernel['p90_lag']} 天，压批/迟到明显，接收日曲线失真大。"})
        else:
            items.append({"tone": "good", "code": "timely",
                          "text": f"报送及时，平均延迟 {kernel['mean_lag']} 天，当日达比例 {kernel['same_day_ratio']*100:.0f}%。"})
        if peak_day_share >= 0.18:
            score -= 8
            items.append({"tone": "warn", "code": "arrival_concentration",
                          "text": f"签收高度集中：最忙签收日占全部 {peak_day_share*100:.0f}%，易形成人为峰值。"})
        # 月末/超长合并批次漂移
        long_batch = db.query(ClearingBatch).filter(
            ClearingBatch.org_code == p.org_code).all()
        widest = max((len(b.service_dates or {}) for b in long_batch), default=1)
        if widest >= 5:
            score -= 5
            items.append({"tone": "warn", "code": "month_end_merge_drift",
                          "text": f"存在覆盖 {widest} 个服务日的合并/月末批次，月末延迟机制相对日常略有漂移；反卷积区间在月末更宽，批次对账仍精确。"})

        score = max(35, min(100, score))
        level = "good" if score >= 88 else ("warn" if score >= 70 else "bad")
        cards.append({
            "code": p.code, "org_code": p.org_code, "org_name": p.org_name,
            "report_mode": p.report_mode,
            "report_mode_label": {
                "daily_stable": "每日稳定直报",
                "batch_catchup": "多日压批·固定补传",
                "service_time_reliable": "无批次号·服务时间可靠",
            }.get(p.report_mode, p.report_mode),
            "mechanism_desc": p.mechanism_desc,
            "quality_score": score, "level": level,
            "metrics": {
                "total_units": total, "known_service_units": known,
                "unknown_units": unknown,
                "service_time_completeness": svc_comp,
                "batch_no_coverage": batch_cov,
                "mean_lag_days": kernel["mean_lag"],
                "same_day_ratio": kernel["same_day_ratio"],
                "peak_signoff_day_share": peak_day_share,
                "delay_samples": kernel["samples"],
            },
            "delay_histogram": kernel["histogram"],
            "batch_correction": {
                "applicable": applicable, "reason_code": reason_code,
                "reason": BATCH_UNAVAILABLE.get(reason_code, "") if not applicable else "可按批次清单对账还原。",
                "cross_org_guard": guard,
            },
            "feedback": items,
        })
    return cards


# ---------------------------------------------------------------- 主编排
def reconstruct(db: Session, org_code: str = "ALL", kind: str = "visit",
                start: date | None = None, end: date | None = None) -> dict:
    start = start or TARGET_START
    end = end or TARGET_END
    dates = daterange(start, end)
    rows = load_rows(db, org_code, kind)

    if rows:
        horizon = max(end, max(r.signed_at.date() for r in rows))
    else:
        horizon = end
    axis = daterange(start, horizon)

    received_all = _received_by_signoff(rows, axis)
    direct = _service_by_date(rows, dates)
    unknown = _unknown(rows)

    # 画像（单机构时给出）
    profile = None
    if org_code != "ALL":
        profile = db.query(SourceProfile).filter_by(org_code=org_code).first()

    kernel = delay_kernel(rows)
    scheduled_orgs = {p.org_code for p in load_profiles(db)
                      if p.report_mode == "batch_catchup"}
    deconv = deconv_for_scope(rows, dates, scheduled_orgs)
    batch = reconcile_batches(db, rows, dates, kind, org_code)

    peaks = interpret_peaks(
        rows, dates, axis, received_all, direct, db, kind)

    total_units = int(sum(int(r.unit_count or 1) for r in rows))
    known_total = int(sum(direct))

    # 覆盖区间：直接证据覆盖了目标期哪些服务日；以及在最大经验延迟下，
    # 反卷积到哪一天才算“延迟到达已被完整观测”（右缘可能删失）。
    evidenced_idx = [i for i, v in enumerate(direct) if v > 0]
    k_max = kernel["max_lag"]
    observable_through = min(end, horizon - timedelta(days=k_max))
    coverage = {
        "target_start": start.isoformat(), "target_end": end.isoformat(),
        "target_days": len(dates),
        "direct_evidenced_days": len(evidenced_idx),
        "direct_coverage_ratio": round(len(evidenced_idx) / len(dates), 3),
        "evidenced_from": dates[evidenced_idx[0]].isoformat() if evidenced_idx else None,
        "evidenced_to": dates[evidenced_idx[-1]].isoformat() if evidenced_idx else None,
        "arrival_horizon": horizon.isoformat(),
        "empirical_max_lag_days": k_max,
        "deconv_observable_through": observable_through.isoformat(),
        "right_edge_possibly_censored_days": max(
            0, (end - observable_through).days),
        "uncertainty": "每日 [low, high] 与总量区间即估计的覆盖/不确定范围；"
                       "缺失服务时间只计入总量上界，不进入日曲线。",
    }

    return {
        "org": org_code, "kind": kind,
        "kind_label": KIND_LABEL.get(kind, kind),
        "target": {"start": start.isoformat(), "end": end.isoformat(),
                   "days": len(dates)},
        "coverage": coverage,
        "arrival_axis": [d.isoformat() for d in axis],
        "service_axis": [d.isoformat() for d in dates],
        # 原始平台视角：按签收/接收日（含未知服务时间记录）
        "received_daily": [int(v) for v in received_all],
        # 真值/最可靠视角：按服务发生时点直接汇总（未知不分配）
        "direct_daily": [int(v) for v in direct],
        "unknown": unknown,
        "totals": {"all_units": total_units,
                   "known_service_units": known_total,
                   "unknown_units": unknown["units"]},
        "delay_kernel": {k: v for k, v in kernel.items() if k != "probs"},
        "deconv": deconv,
        "batch": batch,
        "peaks": peaks,
        "profile": {"org_code": profile.org_code, "org_name": profile.org_name,
                    "report_mode": profile.report_mode} if profile else None,
        "disclaimer": "重建曲线用于校正统计口径与核查优先级，不构成欺诈或违法结论。",
    }
