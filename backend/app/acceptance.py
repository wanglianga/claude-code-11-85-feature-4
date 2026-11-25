"""虚构网络验收断言（只读）。

对应题目验收剧本：
P1 双药店同日同成分重叠 + 停诊 + 同一给药期；规则与图模型同链；
P2/P3 弱身份不合并、不扩展；P4 调拨解释合法供药；P5 申诉退回关闭。
"""
from datetime import date

from sqlalchemy.orm import Session

from .models import (
    Appeal, Case, CaseJudgment, GraphEdge, IdentityEvidence, SourceRecord,
)
from .services import identity
from .services.expansion import expand, high_amount_extension
from .services.ranking import rank_cases


def _chk(key, name, passed, expected, actual, detail=""):
    return {"key": key, "name": name, "passed": bool(passed),
            "expected": expected, "actual": actual, "detail": detail}


def run_acceptance(db: Session) -> dict:
    checks = []

    # 1) 来源账 ----------------------------------------------------------
    src_total = db.query(SourceRecord).count()
    no_citation = db.query(SourceRecord).filter(
        (SourceRecord.citation.is_(None)) | (SourceRecord.citation == "")).count()
    old = db.query(SourceRecord).filter_by(code="SRC-ST5B").first()
    corr = db.query(SourceRecord).filter_by(code="SRC-ST5B-C2").first()
    checks.append(_chk(
        "source_ledger", "来源账：记录可引用且更正链完整",
        src_total >= 50 and no_citation == 0 and old and old.status == "corrected"
        and corr and corr.supersedes_id == old.id,
        ">=50 条来源、0 条缺引用、SRC-ST5B 被更正单接续",
        {"total": src_total, "missing_citation": no_citation,
         "old_status": old.status if old else None,
         "correction_linked": bool(corr and old and corr.supersedes_id == old.id)}))

    # 2) 身份解析分级 -----------------------------------------------------
    ie01 = db.query(IdentityEvidence).filter_by(code="IE-01").first()
    ie03 = db.query(IdentityEvidence).filter_by(code="IE-03").first()
    v, p2c, c2m = identity.cluster_map(db)
    p1_same = p2c.get("P1") == p2c.get("P1B")
    p23_sep = p2c.get("P2") != p2c.get("P3")
    checks.append(_chk(
        "identity_grading", "强证据成簇、弱电话候选不合并",
        ie01 and ie01.tier == "strong" and ie01.status == "auto" and p1_same
        and ie03 and ie03.tier == "weak" and ie03.status == "pending" and p23_sep,
        "P1/P1B 同强簇；P2/P3 异簇且 IE-03 pending",
        {"identity_version": v.version if v else None,
         "P1_cluster": p2c.get("P1"), "P1B_cluster": p2c.get("P1B"),
         "P2_cluster": p2c.get("P2"), "P3_cluster": p2c.get("P3"),
         "IE-03_status": ie03.status if ie03 else None}))

    # 3) P1 必要事实 ------------------------------------------------------
    c1 = db.query(Case).filter_by(code="C-001").first()
    j1 = db.query(CaseJudgment).filter_by(id=c1.current_judgment_id).first()
    rr1 = j1.rule_result
    facts = {f["key"]: f for f in rr1["necessary_facts"]}
    chain1 = rr1["support_chain"]
    required_uids = [
        "edge:identity_strong:IE-01",
        "edge:rx_settlement:RX1__ST1", "edge:rx_settlement:RX2__ST2",
        "edge:settle_org:ST1__PH1", "edge:settle_org:ST2__PH2",
        "edge:ingredient_overlap:ST1__ST2",
        "edge:schedule_conflict:RX1__SCH1",
    ]
    missing = [u for u in required_uids if u not in chain1]
    p1_facts_ok = (
        rr1["fired"]
        and facts["distinct_pharmacies"]["passed"]
        and facts["same_day"]["passed"]
        and facts["qty_overlap"]["value"] >= 28
        and facts["same_admin_period"]["passed"]
        and facts["schedule_conflict"]["passed"]
        and not missing
    )
    checks.append(_chk(
        "p1_chain", "P1：两药店同日同成分数量重叠+停诊+同一给药期，支持链完整",
        p1_facts_ok, "全部必要事实成立且 7 条核心边在链",
        {"fired": rr1["fired"],
         "overlap_qty": facts["qty_overlap"]["value"],
         "same_admin_period": facts["same_admin_period"]["passed"],
         "schedule": facts["schedule_conflict"]["passed"],
         "missing_uids": missing, "chain": chain1}))

    # 4) 规则与图模型同链 + 同目标排序 P1 居首 ------------------------------
    mr1 = j1.model_result or {}
    same_chain = mr1.get("trace_chain_uids") == chain1
    open_rows = [r for r in rank_cases(db, "MULTI_POINT", "model")
                 if r["status"] in ("pending", "reviewing", "new")]
    rule_rows = [r for r in rank_cases(db, "MULTI_POINT", "amount_completeness")
                 if r["status"] in ("pending", "reviewing", "new")]
    top_model = open_rows[0]["code"] if open_rows else None
    top_rule = rule_rows[0]["code"] if rule_rows else None
    checks.append(_chk(
        "same_chain_and_rank", "规则与图模型在 P1 上展示相同支持链，且同目标排序居首",
        same_chain and top_model == "C-001" and top_rule == "C-001"
        and (mr1.get("calibrated_score") or 0) >= 55,
        "trace_chain_uids == support_chain；两种排序开放线索第一均为 C-001；模型分≥55",
        {"same_chain": same_chain, "top_model": top_model, "top_rule": top_rule,
         "model_score": mr1.get("calibrated_score"),
         "rule_score": j1.rule_result.get("raw_score")}))

    # 5) P2/P3 弱身份：只给待核边，不扩展 --------------------------------
    exp23 = expand(db, "beneficiary", "P2")
    uids23 = {e["uid"] for e in exp23["edges"]}
    nodes23 = {n["uid"] for n in exp23["nodes"]}
    weak_ok = ("edge:identity_weak:IE-03" in uids23
               and "person:P3" in exp23["terminal_candidates"]
               and "settlement:ST4" not in nodes23
               and "rx:RX4" not in nodes23)
    checks.append(_chk(
        "p23_weak_no_expand", "P2/P3：弱电话候选只生成待核边，不沿 P3 继续扩展",
        weak_ok, "IE-03 在图；P3 为 terminal；ST4/RX4 不可达",
        {"weak_edge_present": "edge:identity_weak:IE-03" in uids23,
         "terminal": exp23["terminal_candidates"],
         "ST4_reached": "settlement:ST4" in nodes23,
         "RX4_reached": "rx:RX4" in nodes23}))

    # 6) P4 调拨解释 + 否定边不扩展 --------------------------------------
    c3 = db.query(Case).filter_by(code="C-003").first()
    j3 = db.query(CaseJudgment).filter_by(id=c3.current_judgment_id).first()
    inv = next((c for c in j3.counter_result.get("checks", [])
                if c["check_type"] == "inventory"), None)
    ov = db.query(GraphEdge).filter(
        GraphEdge.edge_type == "ingredient_overlap",
        GraphEdge.uid.like("%ST5A%ST5B%")).first()
    exp4 = expand(db, "claim", "ST5A")
    exp4_uids = {e["uid"] for e in exp4["edges"]}
    p4_ok = (c3.status == "excluded_legitimate" and inv
             and inv["status"] == "checked_explains" and "TR4" in inv["detail"]
             and ov and ov.status == "negated"
             and "edge:ingredient_overlap:ST5A__ST5B" not in exp4_uids)
    checks.append(_chk(
        "p4_transfer", "P4：调拨+红冲解释表面重复，重叠边否定且不再扩展",
        p4_ok, "C-003 excluded_legitimate；inventory=checked_explains(TR4)；overlap negated 且扩展不可见",
        {"status": c3.status, "inventory": inv and inv["status"],
         "overlap_status": ov and ov.status,
         "overlap_in_expansion": "edge:ingredient_overlap:ST5A__ST5B" in exp4_uids}))

    # 7) P5 申诉退回 -----------------------------------------------------
    app1 = db.query(Appeal).filter_by(code="APP1").first()
    c4 = db.query(Case).filter_by(code="C-004").first()
    j4 = db.query(CaseJudgment).filter_by(id=c4.current_judgment_id).first()
    ap = next((c for c in j4.counter_result.get("checks", [])
               if c["check_type"] == "appeal"), None)
    checks.append(_chk(
        "p5_appeal_return", "P5：申诉成立且确认药品退回，线索关闭",
        app1 and app1.result == "upheld" and app1.drug_returned
        and c4.status == "closed_returned" and ap and ap["status"] == "confirmed"
        and ap["effect"] == "close",
        "APP1 upheld/returned；C-004 closed_returned；appeal check confirmed/close",
        {"appeal": app1.result if app1 else None,
         "returned": app1.drug_returned if app1 else None,
         "case_status": c4.status, "check": ap and ap["status"]}))

    # 8) 默认受限扩展 ----------------------------------------------------
    exp1 = expand(db, "beneficiary", "P1")
    cutoff = date(2026, 3, 31)
    bad_dates = [e["uid"] for e in exp1["edges"] if e["event_date"]
                 and not (date.fromisoformat("2025-10-03")
                          <= date.fromisoformat(e["event_date"]) <= cutoff)]
    n1 = {n["uid"] for n in exp1["nodes"]}
    bounds_ok = (exp1["params"]["max_hops"] == 2 and exp1["params"]["window_days"] == 180
                 and not bad_dates and "schedule:SCH1" in n1
                 and "org:PH1" in n1 and "org:PH2" in n1)
    checks.append(_chk(
        "restricted_expansion", "默认扩展：同强簇、两跳、180 日，主链节点可达且无越窗边",
        bounds_ok, "hops=2/window=180；SCH1/PH1/PH2 可达；无越窗边",
        {"node_count": len(exp1["nodes"]), "edge_count": len(exp1["edges"]),
         "out_of_window": bad_dates}))

    # 9) 高金额跨机构扩展门槛 --------------------------------------------
    low = high_amount_extension(db, "C-001", freeze=False)
    high = high_amount_extension(db, "C-007", freeze=False)
    bed_reached = any(n["node_type"] == "bed" for n in high.get("nodes", []))
    checks.append(_chk(
        "high_amount_gate", "高金额跨机构扩展须模式显式授权：P1 被拒、C-007 授权并加入床位边",
        low["authorized"] is False and high["authorized"] is True and bed_reached,
        "C-001 金额不足拒绝；C-007 授权且 bed 节点可达",
        {"c001": low.get("reason"), "c007_authorized": high.get("authorized"),
         "added": high.get("extension", {}).get("added_edge_types"),
         "bed_reached": bed_reached}))

    # 10) 快照与不可变判断 ------------------------------------------------
    from .models import GraphSnapshot
    snap1 = db.query(GraphSnapshot).filter_by(snapshot_no=1).first()
    snap_ok = (snap1 and snap1.cutoff_date.isoformat() == "2026-03-31"
               and snap1.identity_version >= 1 and snap1.policy_versions
               and snap1.graph.get("nodes") and j1.version == 1
               and j1.snapshot_no == 1 and j1.disclaimer)
    checks.append(_chk(
        "snapshot_immutable", "快照 #1 冻结四要素，首份判断不可变且带免责声明",
        snap_ok, "cutoff/identity_version/edge_scope/policy_versions 齐全；judgment v1@snap1",
        {"cutoff": snap1.cutoff_date.isoformat() if snap1 else None,
         "identity_version": snap1.identity_version if snap1 else None,
         "policies": len(snap1.policy_versions) if snap1 else 0,
         "judgment_version": j1.version, "snapshot_no": j1.snapshot_no}))

    # 11) 服务活动时间重建：A/B/C 来源画像与五要素 ------------------------
    from .models import ActivityRecord, ClearingBatch, SourceProfile
    from .services import activity as act

    profs = {p.org_code: p for p in db.query(SourceProfile).all()}
    pa, pb, pc = profs.get("H-A"), profs.get("H-B"), profs.get("H-C")
    b_missing_seq = db.query(ActivityRecord).filter(
        ActivityRecord.org_code == "H-B",
        ActivityRecord.batch_seq.is_(None)).count()
    missing_signed = db.query(ActivityRecord).filter(
        ActivityRecord.signed_at.is_(None)).count()
    visit_n = db.query(ActivityRecord).filter_by(kind="visit").count()
    settle_n = db.query(ActivityRecord).filter_by(kind="settlement").count()
    rs_settle = act.reconstruct(db, "ALL", "settlement")
    profiles_ok = (
        pa and pb and pc
        and pa.report_mode == "daily_stable"
        and pb.report_mode == "batch_catchup" and pb.has_batch_no
        and pb.batch_metadata_complete
        and pc.report_mode == "service_time_reliable"
        and pc.has_batch_no is False and pc.service_time_reliable
        and b_missing_seq == 0 and missing_signed == 0
        and visit_n > 60 and settle_n > 60
        and len(rs_settle["direct_daily"]) == 31
        and rs_settle["totals"]["known_service_units"] > 0)
    checks.append(_chk(
        "activity_profiles", "活动重建：A每日/B压批/C无批次 三画像与机构·批次·服务时间·签收·序号齐全",
        profiles_ok,
        "H-A daily_stable；H-B batch_catchup+完整批次+序号；H-C 无批次但服务时间可靠；就诊/结算两类",
        {"profiles": sorted(profs), "B缺序号行": b_missing_seq,
         "缺签收时间行": missing_signed, "visit_rows": visit_n,
         "settlement_rows": settle_n}))

    # 12) 接收日峰值 vs 校正后真实服务峰值分离 -----------------------------
    rb = act.reconstruct(db, "H-B", "visit")
    axis_b = rb["arrival_axis"]
    i_recv = axis_b.index("2026-03-16")
    i_svc = rb["service_axis"].index("2026-03-16")
    i_wed2 = rb["service_axis"].index("2026-03-13")
    we2 = db.query(ClearingBatch).filter_by(batch_no="QPB-WE2-03").first()
    we2_visit = sum(v.get("visit", 0) for v in (we2.service_dates or {}).values())
    b_peak = rb["peaks"]
    peak_ok = (
        we2 is not None and we2.metadata_complete
        and rb["received_daily"][i_recv] == we2_visit
        and rb["received_daily"][i_recv] >= 4 * rb["direct_daily"][i_svc]
        and b_peak["raw_peak"]["date"] == "2026-03-16"
        and b_peak["raw_peak_attenuation"] is not None
        and b_peak["raw_peak_attenuation"] >= 0.7
        and b_peak["service_peak"]["date"] in ("2026-03-12", "2026-03-13")
        and b_peak["peak_shift"]["from"] != b_peak["peak_shift"]["to"]
        and any(p["verdict"] == "transmission_batch_peak"
                for p in rb["peaks"]["transmission_peaks"])
        and any(p["verdict"] == "real_service_peak"
                for p in rb["peaks"]["real_peaks"]))
    checks.append(_chk(
        "activity_peak_separation",
        "峰值分离：B 的 3/16 接收峰=周末四天压批，校正后落到 3/12-13 真实服务峰",
        peak_ok,
        "3/16 接收量==QPB-WE2 清单量且≥当天真实服务4倍；衰减≥70%；真峰在3/12-13；两类解释齐全",
        {"recv_0316": rb["received_daily"][i_recv],
         "we2_manifest_visit": we2_visit,
         "service_0316": rb["direct_daily"][i_svc],
         "service_0313": rb["direct_daily"][i_wed2],
         "attenuation": b_peak["raw_peak_attenuation"],
         "raw_peak": b_peak["raw_peak"], "service_peak": b_peak["service_peak"]}))

    # 13) 两条方法估计同一目标期间 + 不确定范围，批次更窄 -------------------
    dv, bm = rb["deconv"], rb["batch"]
    same_window = (len(dv["daily"]) == len(bm["daily"]) == 31
                   and rb["target"]["start"] == "2026-03-01"
                   and rb["target"]["end"] == "2026-03-31")
    coverage_ok = (rb["coverage"]["target_days"] == 31
                   and rb["coverage"]["direct_evidenced_days"] == 31
                   and rb["coverage"]["direct_coverage_ratio"] == 1.0
                   and rb["coverage"]["deconv_observable_through"])
    ordering = (dv["total"]["low"] <= dv["total"]["estimate"] <= dv["total"]["high"]
                and bm["total"]["low"] <= bm["total"]["estimate"] <= bm["total"]["high"])
    mass_ok = abs(dv["total"]["estimate"]
                  - rb["totals"]["known_service_units"]) <= 1
    batch_exact = bm["available"] and bm["total"]["estimate"] == 477 \
        and bm["total"]["low"] == bm["total"]["high"] == 477
    narrower = ((bm["total"]["high"] - bm["total"]["low"])
                < (dv["total"]["high"] - dv["total"]["low"]))
    methods_ok = (same_window and coverage_ok and ordering and mass_ok
                  and batch_exact and narrower)
    checks.append(_chk(
        "activity_two_methods",
        "两法同期：延迟反卷积与批次对账都估计 3 月服务量并给区间与覆盖区间，完整批次精确且区间更窄",
        methods_ok,
        "两法均 31 天同一目标期；直接证据覆盖31/31；反卷积总质量守恒≈477；批次=477 点区间；批次宽度<反卷积",
        {"target": rb["target"], "coverage": rb["coverage"],
         "deconv_total": dv["total"],
         "batch_total": bm["total"],
         "deconv_width": dv["total"]["high"] - dv["total"]["low"],
         "batch_width": bm["total"]["high"] - bm["total"]["low"]}))

    # 14) C 不能套用 B 的批次校正 ------------------------------------------
    rc = act.reconstruct(db, "H-C", "visit")
    pc_obj = db.query(SourceProfile).filter_by(org_code="H-C").first()
    guard = act.why_cannot_transfer_batch("H-B", pc_obj)
    c_batch_rows = db.query(ClearingBatch).filter_by(org_code="H-C").count()
    c_null_batch = db.query(ActivityRecord).filter(
        ActivityRecord.org_code == "H-C",
        ActivityRecord.batch_no.isnot(None)).count()
    c_ok = (rc["batch"]["available"] is False
            and rc["batch"]["reason_code"] == "no_batch_no"
            and c_batch_rows == 0 and c_null_batch == 0
            and guard["allowed"] is False
            and guard["reason_code"] == "target_has_no_batch_no"
            and rc["totals"]["known_service_units"] > 0
            and any(o["org_code"] == "H-C" and o["reliability"] == "high"
                    for o in rc["deconv"]["by_org"]))
    checks.append(_chk(
        "activity_c_no_batch_guard",
        "C 防护：无批次号→批次对账不可用，且明确禁止照搬 B 的周一/四天批次校正",
        c_ok,
        "C batch.available=false/no_batch_no；跨机构套用被 guard 拒绝；C 靠可靠服务时间/自身平滑核",
        {"batch": rc["batch"]["reason_code"], "clearing_batches": c_batch_rows,
         "rows_with_batch_no": c_null_batch, "guard": guard["reason_code"],
         "guard_reason": guard["reason"]}))

    # 15) 缺失服务时间保留未知，不分配到月末 --------------------------------
    ra = act.reconstruct(db, "H-A", "visit")
    unk_db = db.query(ActivityRecord).filter(
        ActivityRecord.service_date.is_(None)).all()
    unknown_rows = [r for r in unk_db if r.kind == "visit"]
    unk_units = sum(r.unit_count for r in unknown_rows)
    unk_service_contrib = sum(act._service_by_date(unknown_rows, act.daterange(
        act.TARGET_START, act.TARGET_END)))
    i_0331 = ra["service_axis"].index("2026-03-31")
    unknown_ok = (
        unk_units == 7 and ra["unknown"]["units"] == 7
        and ra["unknown"]["by_signoff"].get("2026-03-31") == 7
        and unk_service_contrib == 0
        and sum(ra["direct_daily"]) == ra["totals"]["known_service_units"]
        and ra["totals"]["all_units"] - ra["totals"]["known_service_units"] == 7
        and ra["direct_daily"][i_0331] > 0)
    checks.append(_chk(
        "activity_unknown_preserved",
        "缺失服务时间：7 笔月末签收但服务时间未知，只入 unknown，不分配到 3/31 或任何日",
        unknown_ok,
        "unknown=7 且均签收 3/31；未知行对所有服务日贡献=0；direct 合计=known=539，all=546",
        {"unknown_units": unk_units,
         "by_signoff": ra["unknown"]["by_signoff"],
         "unknown_service_day_contribution": unk_service_contrib,
         "direct_sum": sum(ra["direct_daily"]),
         "known": ra["totals"]["known_service_units"],
         "all": ra["totals"]["all_units"],
         "direct_0331_not_dumped": ra["direct_daily"][i_0331]}))

    passed = sum(1 for c in checks if c["passed"])
    return {"overall": passed == len(checks), "passed": passed,
            "total": len(checks), "checks": checks}
