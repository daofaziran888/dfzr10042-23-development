"""供应商归属对账：合同归属、生效日、账单流转与月报的端到端测试。"""
from __future__ import annotations

import threading

import pytest

from app.domain.supplier_recon import (
    CONTRACT_REVOKED,
    ELIGIBLE,
    EXPIRED_ORDER,
    OUT_OF_PERIOD,
    REMOVED,
    UNAUTHORIZED,
    WRONG_DEPARTMENT,
    Contract,
    ContractCorrection,
    ReconciliationLine,
    evaluate_line,
    period_bounds,
)
from app.services.supplier_recon import (
    BILL_CLAIMED,
    BILL_PENDING,
    BILL_RETURNED,
    BILL_SIGNED,
    SupplierReconService,
)


@pytest.fixture
def service() -> SupplierReconService:
    return SupplierReconService()


# ---------------------------------------------------------------- 纯核验
class TestPeriodBounds:
    def test_expands_month_to_closed_range(self):
        assert period_bounds("2026-11") == ("2026-11-01", "2026-11-30")

    def test_handles_december_and_february(self):
        assert period_bounds("2026-12") == ("2026-12-01", "2026-12-31")
        assert period_bounds("2028-02") == ("2028-02-01", "2028-02-29")

    def test_rejects_bad_period(self):
        with pytest.raises(ValueError):
            period_bounds("2026-13")
        with pytest.raises(ValueError):
            period_bounds("202611")


class TestContractWindow:
    def _contract(self, end="2026-12-05") -> Contract:
        return Contract(
            contract_id="HT",
            company="甲公司",
            department="采购部",
            supplier="华东电子",
            start_date="2026-11-01",
            end_date=end,
        )

    def test_end_date_is_inclusive(self):
        assert self._contract().date_status("2026-12-05") == "ok"

    def test_after_end_date_is_expired(self):
        assert self._contract().date_status("2026-12-06") == "expired"

    def test_before_start_is_insufficient(self):
        assert self._contract().date_status("2026-10-31") == "insufficient"


class TestEvaluateLine:
    def _line(self, **overrides) -> ReconciliationLine:
        base = dict(
            line_id="L1",
            order_key="SO-1",
            company="甲公司",
            department="采购部",
            supplier="华东电子",
            amount=60.0,
            trade_date="2026-11-18",
            contract_id="HT",
        )
        base.update(overrides)
        return ReconciliationLine(**base)

    def _contracts(self) -> list[Contract]:
        return [
            Contract(
                contract_id="HT",
                company="甲公司",
                department="采购部",
                supplier="华东电子",
                start_date="2026-11-01",
                end_date="2026-12-05",
            )
        ]

    def test_eligible_when_all_checks_pass(self):
        result = evaluate_line(
            self._line(), contracts=self._contracts(), corrections=[], period="2026-11"
        )
        assert result.eligible and result.reason == ELIGIBLE
        assert result.amount == 60.0

    def test_out_of_period_before_contract_check(self):
        result = evaluate_line(
            self._line(trade_date="2026-12-01"),
            contracts=self._contracts(),
            corrections=[],
            period="2026-11",
        )
        assert result.reason == OUT_OF_PERIOD

    def test_expired_order_distinct_from_wrong_department(self):
        expired = evaluate_line(
            self._line(trade_date="2026-12-06"),
            contracts=self._contracts(),
            corrections=[],
            period="2026-12",
        )
        wrong = evaluate_line(
            self._line(company="乙公司", department="采购部"),
            contracts=self._contracts(),
            corrections=[],
            period="2026-11",
            report_company="乙公司",
            report_department="采购部",
        )
        assert expired.reason == EXPIRED_ORDER
        assert wrong.reason == WRONG_DEPARTMENT
        assert expired.reason_label != wrong.reason_label

    def test_foreign_company_line_rejected_for_report_department(self):
        result = evaluate_line(
            self._line(company="乙公司", department="采购部"),
            contracts=self._contracts(),
            corrections=[],
            period="2026-11",
            report_company="甲公司",
            report_department="采购部",
        )
        assert result.reason == WRONG_DEPARTMENT

    def test_correction_effective_before_trade_revokes(self):
        correction = ContractCorrection(
            correction_id="CORR-1",
            contract_id="HT",
            effective_date="2026-11-01",
            reason="结算主体更正",
            revoked_order_keys=("SO-1",),
        )
        result = evaluate_line(
            self._line(),
            contracts=self._contracts(),
            corrections=[correction],
            period="2026-11",
        )
        assert result.reason == CONTRACT_REVOKED
        assert "CORR-1" in result.detail

    def test_correction_effective_after_trade_does_not_revoke(self):
        correction = ContractCorrection(
            correction_id="CORR-2",
            contract_id="HT",
            effective_date="2026-11-20",
            reason="事后更正",
            revoked_order_keys=("SO-1",),
        )
        result = evaluate_line(
            self._line(),
            contracts=self._contracts(),
            corrections=[correction],
            period="2026-11",
        )
        assert result.eligible

    def test_unauthorized_line_blocked(self):
        result = evaluate_line(
            self._line(unauthorized=True),
            contracts=self._contracts(),
            corrections=[],
            period="2026-11",
        )
        assert result.reason == UNAUTHORIZED

    def test_removed_line_marked_removed(self):
        result = evaluate_line(
            self._line(removed=True),
            contracts=self._contracts(),
            corrections=[],
            period="2026-11",
        )
        assert result.reason == REMOVED


# ---------------------------------------------------------------- 关联入口
class TestAssociationView:
    def test_same_order_number_shows_both_companies(self, service):
        view = service.association_view(period="2026-11", order_key="SO-20261101")
        assert len(view["orders"]) == 1
        order = view["orders"][0]
        assert order["companies"] == ["乙公司", "甲公司"] or order["companies"] == ["甲公司", "乙公司"]
        by_company = {row["公司"]: row for row in order["rows"]}
        assert by_company["甲公司"]["金额"] == 60.0
        assert by_company["乙公司"]["金额"] == 25.0

    def test_jia_report_counts_only_jia_valid_amount(self, service):
        view = service.association_view(period="2026-11", company="甲公司", order_key="SO-20261101")
        rows = view["orders"][0]["rows"]
        eligible = [row for row in rows if row["入账资格"] == "有"]
        assert [row["金额"] for row in eligible] == [60.0]
        own = next(row for row in rows if row["公司"] == "甲公司")
        other = next(row for row in rows if row["公司"] == "乙公司")
        assert own["入账资格"] == "有"
        assert other["拒绝原因"] == "错部门"

    def test_date_bounds_exposed(self, service):
        view = service.association_view(period="2026-11")
        assert view["date_bounds"] == {"start": "2026-11-01", "end": "2026-11-30"}
        assert view["summary"]["reason_labels"]["EXPIRED_ORDER"] == "过期订单"

    def test_expired_and_wrong_department_are_distinguishable(self, service):
        view = service.association_view(period="2026-11", company="甲公司")
        reasons = view["summary"]["reason_counts"]
        assert reasons[EXPIRED_ORDER] >= 1
        assert reasons[WRONG_DEPARTMENT] >= 1

    def test_rejection_catalog_covers_all_codes(self, service):
        catalog = service.association_view(period="2026-11")["summary"]["reason_labels"]
        for code in (ELIGIBLE, OUT_OF_PERIOD, EXPIRED_ORDER, WRONG_DEPARTMENT,
                     CONTRACT_REVOKED, UNAUTHORIZED, REMOVED):
            assert code in catalog


# ---------------------------------------------------------------- 批次与账单
class TestBatchAndBill:
    def test_batch_only_admits_eligible_lines(self, service):
        result = service.create_batch(period="2026-11", company="甲公司")
        batch = result["batch"]
        assert batch["payable"] == 75.0  # 60 + 15，不含过期/错部门/越权/跨期
        assert result["bill"]["line_ids"] == ["L-0001", "L-0004"]

    def test_batch_is_idempotent_and_refreshes(self, service):
        first = service.create_batch(period="2026-11", company="甲公司")
        second = service.create_batch(period="2026-11", company="甲公司")
        assert first["created"] is True
        assert second["created"] is False
        assert second["batch"]["batch_id"] == first["batch"]["batch_id"]

    def test_bill_lifecycle(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        ok, message, bill = service.claim_bill(bill_id)
        assert ok and bill["status"] == BILL_CLAIMED
        assert bill["can_sign"] is True
        ok, message, bill = service.return_bill(bill_id, reason="需要复核")
        assert ok and bill["status"] == BILL_RETURNED
        ok, _, bill = service.claim_bill(bill_id)
        assert ok and bill["status"] == BILL_CLAIMED

    def test_cannot_sign_unclaimed(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        ok, message, _ = service.sign_bill(bill_id)
        assert not ok
        assert service.list_ledger() == []


# ---------------------------------------------------------------- 合同更正
class TestCorrectionAndReturn:
    def test_correction_revokes_then_remove_signs_15(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        # 生效日 11-01 早于交易 11-18：撤回原 60 元资格
        correction = service.apply_correction(
            contract_id="HT-JIA-2026",
            effective_date="2026-11-01",
            reason="结算主体更正",
            revoked_order_keys=["SO-20261101"],
        )
        assert correction["refreshed_batches"][0]["revoked_line_ids"] == ["L-0001"]
        bill = service.get_bill(bill_id)
        assert bill["payable"] == 15.0
        ok, message, _ = service.sign_bill(bill_id)
        assert not ok and "L-0001" in message
        # 财务剔除已撤行后签发 15 元
        ok, _, bill = service.remove_line(bill_id, "L-0001", reason="合同更正撤回")
        assert ok and bill["payable"] == 15.0
        ok, message, result = service.sign_bill(bill_id)
        assert ok
        assert result["entry"]["amount"] == 15.0
        assert result["entry"]["basis"][0]["行号"] == "L-0004"

    def test_return_path_leaves_period_unbooked_until_reclaim(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        service.apply_correction(
            contract_id="HT-JIA-2026",
            effective_date="2026-11-01",
            reason="结算主体更正",
            revoked_order_keys=["SO-20261101"],
        )
        service.return_bill(bill_id, reason="合同有更正，退回复核")
        report = service.generate_report(period="2026-11")
        assert report["totals"]["booked"] == 0.0
        assert report["totals"]["unsigned_bill_count"] == 1
        # 重新领取前不得签发
        ok, _, _ = service.sign_bill(bill_id)
        assert not ok
        service.claim_bill(bill_id)
        service.remove_line(bill_id, "L-0001", reason="合同更正撤回")
        ok, _, _ = service.sign_bill(bill_id)
        assert ok
        assert service.generate_report(period="2026-11")["totals"]["booked"] == 15.0

    def test_correction_effective_after_trade_keeps_eligibility(self, service):
        service.create_batch(period="2026-11", company="甲公司")
        service.apply_correction(
            contract_id="HT-JIA-2026",
            effective_date="2026-11-19",
            reason="事后补录",
            revoked_order_keys=["SO-20261101"],
        )
        batch = service.list_batches(period="2026-11")[0]
        assert batch["payable"] == 75.0


# ---------------------------------------------------------------- 越权
class TestUnauthorized:
    def test_unauthorized_before_sign_cannot_enter_payable(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        # L-0005 本来就判越权，未进账单；这里把另一条关联在签发前改成越权
        outcome = service.mark_unauthorized("L-0004", reason="跨主体关联")
        assert outcome["changed"] is True
        bill = service.get_bill(bill_id)
        assert bill["payable"] == 60.0
        assert any(blocker["行号"] == "L-0004" for blocker in bill["blockers"])
        ok, message, _ = service.sign_bill(bill_id)
        assert not ok
        assert all(
            entry["amount"] != 75.0 for entry in service.list_ledger()
        )

    def test_unauthorized_line_never_in_batch_payable(self, service):
        service.create_batch(period="2026-11", company="甲公司")
        batch = service.list_batches(period="2026-11")[0]
        unauthorized_rows = [
            row for row in batch["checks"] if row["拒绝码"] == UNAUTHORIZED
        ]
        assert unauthorized_rows
        assert all(row["入账资格"] == "无" for row in unauthorized_rows)


# ---------------------------------------------------------------- 并发与复算
class TestConcurrencyAndRecompute:
    def test_concurrent_sign_booked_once(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        results: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            ok, _, _ = service.sign_bill(bill_id)
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(results)
        entries = [entry for entry in service.list_ledger() if entry["bill_id"] == bill_id]
        assert len(entries) == 1
        assert entries[0]["amount"] == 75.0

    def test_signed_history_recomputes_from_frozen_basis(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        service.sign_bill(bill_id)
        entry_id = service.list_ledger()[0]["entry_id"]
        # 签发后合同再更正，历史入账记录仍按签发时口径复算
        service.mark_unauthorized("L-0001", reason="签发后才发现的越权")
        recompute = service.recompute_entry(entry_id)
        assert recompute["matches"] is True
        assert recompute["signed_amount"] == 75.0
        assert recompute["recomputed_amount"] == 75.0

    def test_signed_batch_payable_is_frozen_but_drift_is_reported(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        service.sign_bill(bill_id)
        batch = service.list_batches(period="2026-11")[0]
        assert batch["payable"] == 75.0
        assert batch["signed_payable"] == 75.0
        outcome = service.apply_correction(
            contract_id="HT-JIA-2026",
            effective_date="2026-11-01",
            reason="签发后更正",
            revoked_order_keys=["SO-20261101"],
        )
        drift = outcome["refreshed_batches"][0]["post_sign_drift"]
        assert drift == -60.0  # 当前口径少了被撤回的 60 元
        after = service.list_batches(period="2026-11")[0]
        assert after["payable"] == 75.0  # 已签发金额不回改
        assert after["current_payable"] == 15.0
        report = service.generate_report(period="2026-11")
        assert report["totals"]["payable"] == 75.0
        assert report["totals"]["booked"] == 75.0


# ---------------------------------------------------------------- 月报
class TestPayableReport:
    def test_report_uses_batch_eligibility_and_receipts(self, service):
        jia = service.create_batch(period="2026-11", company="甲公司")
        jia_bill = jia["bill"]["bill_id"]
        service.claim_bill(jia_bill)
        service.sign_bill(jia_bill)

        yi = service.create_batch(period="2026-11", company="乙公司")
        report = service.generate_report(period="2026-11")

        assert report["template"] == "应付对账月报"
        receipts = {
            (item["company"], item["department"]): item
            for item in report["department_receipts"]
        }
        assert receipts[("甲公司", "采购部")]["payable"] == 75.0
        assert receipts[("甲公司", "采购部")]["booked"] == 75.0
        assert receipts[("乙公司", "销售部")]["payable"] == 75.0
        assert receipts[("乙公司", "销售部")]["booked"] == 0.0
        assert report["totals"]["booked"] == 75.0
        assert report["totals"]["unbooked"] == 75.0

    def test_report_lists_unsigned_and_rejections(self, service):
        bill_id = service.create_batch(period="2026-11", company="甲公司")["bill"]["bill_id"]
        service.claim_bill(bill_id)
        service.apply_correction(
            contract_id="HT-JIA-2026",
            effective_date="2026-11-01",
            reason="结算主体更正",
            revoked_order_keys=["SO-20261101"],
        )
        service.sign_bill(bill_id)  # 被拒绝，留下记录
        report = service.generate_report(period="2026-11")
        assert report["totals"]["unsigned_bill_count"] == 1
        assert report["totals"]["booked"] == 0.0
        assert report["sign_rejections"]
        assert report["sign_rejections"][0]["reasons"]
        unsigned = report["unsigned_bills"][0]
        assert unsigned["status"] == BILL_CLAIMED
        assert any(row["行号"] == "L-0001" for row in unsigned["blockers"])

    def test_report_batch_lines_only_contain_proven_rows(self, service):
        service.create_batch(period="2026-11", company="甲公司")
        report = service.generate_report(period="2026-11")
        proven = [row for row in report["batch_lines"] if row["入账资格"] == "有"]
        assert sum(row["金额"] for row in proven) == 75.0
        assert all(row["拒绝原因"] == "—" for row in proven)

    def test_report_is_retrievable_and_listed(self, service):
        service.create_batch(period="2026-11", company="甲公司")
        report = service.generate_report(period="2026-11")
        assert service.get_report(report["report_id"]) == report
        listed = service.list_reports()
        assert listed[0]["report_no"] == report["report_no"]

    def test_report_without_batches_is_empty_but_valid(self, service):
        report = service.generate_report(period="2026-10")
        assert report["totals"]["payable"] == 0.0
        assert report["batches"] == []
