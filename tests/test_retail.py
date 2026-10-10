import datetime as dt

import pandas as pd
import pytest
from conftest import load_day, pvpc_text, synthetic_day

from negprice import pvpc, retail
from negprice import summary as sm
from negprice import tariffs as tf

A = retail.Assumptions()
FRI = "2026-10-09"


def at(est: pd.DataFrame, hhmm: str) -> pd.Series:
    return est[est["local_start"] == hhmm].iloc[0]


def test_hand_computed_20td_p1_defaults():
    # energy = (omie + 28 + 0.8) / 1000 × 1.16 + 0.010; regulated = 0.033261 + 0.064292 = 0.097553
    est = retail.estimate(synthetic_day(FRI, [0.0] * 44 + [100.0] * 52), "2.0TD", A)
    zero, hundred = at(est, "10:00"), at(est, "11:00")
    assert zero["period"] == hundred["period"] == "P1"
    assert zero["energy_eur_kwh"] == pytest.approx(0.043408, abs=1e-9)
    assert zero["pre_tax_eur_kwh"] == pytest.approx(0.140961, abs=1e-9)
    assert zero["iee_eur_kwh"] == pytest.approx(0.0072069078596352, abs=1e-9)
    assert zero["iva_eur_kwh"] == pytest.approx(0.031115260650523392, abs=1e-9)
    assert zero["estimate_eur_kwh"] == pytest.approx(0.179283168510158592, abs=1e-9)
    assert hundred["energy_eur_kwh"] == pytest.approx(0.159408, abs=1e-9)
    assert hundred["estimate_eur_kwh"] == pytest.approx(0.326819349064910592, abs=1e-9)
    assert (zero["peaje_eur_kwh"], zero["cargo_eur_kwh"], zero["capacity_eur_kwh"]) == (
        0.033261,
        0.064292,
        0.0008,
    )


def test_iva_is_charged_on_top_of_iee():
    s = retail.price_stack(100.0, 0.033261, 0.064292, 0.0008, A)
    assert s["iva_eur_kwh"] == pytest.approx((s["pre_tax_eur_kwh"] + s["iee_eur_kwh"]) * 0.21, abs=1e-12)
    assert s["iva_eur_kwh"] > s["pre_tax_eur_kwh"] * 0.21
    assert s["estimate_eur_kwh"] == pytest.approx(s["pre_tax_eur_kwh"] * 1.0511269632 * 1.21, abs=1e-12)
    no_tax = retail.price_stack(100.0, 0.033261, 0.064292, 0.0008, retail.Assumptions(iee=0, iva=0))
    assert no_tax["estimate_eur_kwh"] == pytest.approx(0.256961, abs=1e-9)


def test_negative_omie_flows_through():
    est = retail.estimate(synthetic_day(FRI, [-50.0] * 96), "2.0TD", A)
    p1 = at(est, "10:00")
    assert p1["energy_eur_kwh"] == pytest.approx(-0.014592, abs=1e-9)
    assert p1["estimate_eur_kwh"] == pytest.approx(0.105515078232782592, abs=1e-9)
    valle = at(est, "03:00")  # (-50 + 28 + 0) / 1000 × 1.16 + 0.010 + 0.003292, then taxes
    assert valle["pre_tax_eur_kwh"] == pytest.approx(-0.012228, abs=1e-9)
    assert valle["estimate_eur_kwh"] < 0


def test_assumptions_are_configurable_and_annotated():
    d = A.describe()
    assert list(d) == ["loss_factor", "adjustment_services_eur_mwh", "retailer_margin_eur_mwh", "iee", "iva"]
    assert [d[k]["value"] for k in d] == [0.16, 28.0, 10.0, 0.0511269632, 0.21]
    assert all(v["note"] for v in d.values())
    assert all("ASSUMPTION" in d[k]["note"] for k in list(d)[:3])
    base = retail.estimate(synthetic_day(FRI, [50.0] * 96), "2.0TD", A)
    more = retail.estimate(
        synthetic_day(FRI, [50.0] * 96), "2.0TD", retail.Assumptions(retailer_margin_eur_mwh=20)
    )
    diff = more["estimate_eur_kwh"] - base["estimate_eur_kwh"]
    assert diff.tolist() == pytest.approx([0.010 * 1.0511269632 * 1.21] * 96, abs=1e-12)


def test_default_assumptions_match_their_derivation():
    """loss_factor and adjustment services defaults vs the two ESIOS fixture days they were derived from."""
    ratios, services = [], []
    for day in ("20261009", "20261010"):
        date = dt.date(int(day[:4]), int(day[4:6]), int(day[6:]))
        pv = pvpc.parse(pvpc_text(day), date)
        omie = load_day(day) if day == "20261010" else None
        services += (pv["sah_eur_mwh"] + pv["fom_eur_mwh"] + pv["fos_eur_mwh"] + pv["int_eur_mwh"]).tolist()
        if omie is not None:
            hourly = omie.groupby(omie["ts_utc"].dt.floor("h"))["price_es"].mean()
            ratios += [p / o for p, o in zip(pv["pmh_eur_mwh"], hourly, strict=True) if o > 5]
    assert pd.Series(services).median() == pytest.approx(A.adjustment_services_eur_mwh, abs=0.5)
    assert 18.4 <= min(services) and max(services) <= 58.3
    assert pd.Series(ratios).median() == pytest.approx(1 + A.loss_factor, abs=0.01)


def test_columns_labels_and_pvpc_context(golden):
    pv = pvpc.parse(pvpc_text("20261010"), dt.date(2026, 10, 10))
    est = retail.estimate(golden, "2.0TD", A, pv)
    assert list(est.columns) == retail.COLUMNS and len(est) == 96
    assert set(est["period"]) == {"P3"} and set(est["tariff"]) == {"2.0TD"}
    assert est["local_start"].iloc[0] == "00:00" and est["local_start"].iloc[-1] == "23:45"
    assert est["pvpc_eur_kwh"].iloc[0] == pytest.approx(0.21824) and est["pvpc_eur_kwh"].iloc[
        4
    ] == pytest.approx(0.21804)
    assert retail.estimate(golden, "2.0TD", A)["pvpc_eur_kwh"].isna().all()
    h = retail.hourly(est)
    assert len(h) == 24 and list(h["local_hour"]) == list(range(24))
    assert h["estimate_eur_kwh"][0] == pytest.approx(est["estimate_eur_kwh"][:4].mean())
    assert "estimate" in retail.LABEL and "ESTIMATE" in retail.__doc__ and "Estimate" in retail.DISCLAIMER


def test_cheapest_window_retail_vs_wholesale_on_fixture_day(golden):
    """Sat 10 Oct 2026 is all valle, so tolls are flat and both rankings pick the same windows."""
    est = retail.estimate(golden, "2.0TD", A)
    w180 = retail.cheapest_window(est, 180, "omie_eur_mwh")
    r180 = retail.cheapest_window(est, 180)
    ref = sm.cheapest_window(golden, 180)
    assert (w180["start"], w180["end"], w180["omie_eur_mwh"]) == (ref["start"], ref["end"], ref["mean"])
    assert (w180["start"], w180["end"]) == (r180["start"], r180["end"]) == ("13:30", "16:30")
    assert r180["periods"] == ["P3"] and r180["regulated_eur_kwh"] == 0.003292
    r60 = retail.cheapest_window(est, 60)
    assert (r60["start"], r60["end"]) == ("15:00", "16:00")
    assert r60["estimate_eur_kwh"] == pytest.approx(est["estimate_eur_kwh"].iloc[60:64].mean(), abs=1e-6)
    assert retail.compare_windows(est, 180)["same_window"]


def weekday_prices() -> list[float]:
    """Fri: €0 from 11:00 to 14:00 (punta), €2 from 14:00 to 17:00 (llano), €100 otherwise."""
    return [100.0] * 44 + [0.0] * 12 + [2.0] * 12 + [100.0] * 28


@pytest.mark.parametrize("tariff,why", [("2.0TD", "P1 (punta)"), ("3.0TD", "P4")])
def test_weekday_tolls_move_the_cheapest_window(tariff, why):
    est = retail.estimate(synthetic_day(FRI, weekday_prices()), tariff, A)
    cmp = retail.compare_windows(est, 180)
    assert (cmp["wholesale"]["start"], cmp["wholesale"]["end"]) == ("11:00", "14:00")
    assert (cmp["retail"]["start"], cmp["retail"]["end"]) == ("14:00", "17:00")
    assert not cmp["same_window"]
    assert cmp["wholesale"]["estimate_eur_kwh"] > cmp["retail"]["estimate_eur_kwh"]
    line = retail.takeaway(tariff, cmp)
    assert line.startswith(
        f"Cheapest wholesale 3h: 11:00–14:00 (0.00 €/MWh); cheapest estimated {tariff} 3h: 14:00"
    )
    assert f"because 11:00–14:00 is {why}" in line


def test_day_report(golden):
    est = retail.estimate(golden, "3.0TD", A)
    r = retail.day_report(est, A)
    assert r["label"] == "estimate" and r["tariff"] == "3.0TD" and r["date"] == "2026-10-10"
    assert (r["weekday"], r["day_type"], r["season"]) == ("Sat", "D", "baja")
    assert set(r["cheapest_windows"]) == {"60", "180"}
    assert (
        r["min_estimate"]["estimate_eur_kwh"]
        <= r["mean_estimate_eur_kwh"]
        <= r["max_estimate"]["estimate_eur_kwh"]
    )
    assert r["takeaway"].endswith("the same window, all in P6.")
    assert r["assumptions"]["loss_factor"]["note"].startswith("ASSUMPTION")
    assert r["sources"]["peajes"]["boe_id"] == "BOE-A-2025-26348" and r["warnings"] == []
    assert len(r["hourly"]) == 24 and r["pvpc"] == {
        "available": False,
        "mean_eur_kwh": None,
        "note": retail.PVPC_NOTE,
    }
    assert len(r["power_terms_eur_kw_year"]) == 6 and "estimate" in r["power_terms_note"]


def test_year_guard():
    day_2025 = load_day("20251026")
    with pytest.raises(tf.TariffYearError, match="allow_other_year"):
        retail.estimate(day_2025, "2.0TD", A)
    est = retail.estimate(day_2025, "2.0TD", A, allow_other_year=True)
    assert retail.day_report(est, A)["warnings"][0].startswith("WARNING: 2025-10-26 is outside")


def test_spring_forward_day_has_92_periods_and_no_hour_2():
    est = retail.estimate(load_day("20260329"), "2.0TD", A)  # Sunday
    assert len(est) == 92 and 2 not in set(est["local_hour"])
    assert set(est["period"]) == {"P3"}
    assert list(est["local_start"][7:9]) == ["01:45", "03:00"]
    assert len(retail.hourly(est)) == 23
    assert retail.cheapest_window(est, 180)["end"] <= "24:00"


def test_fall_back_day_has_100_periods_and_hour_2_twice():
    day = dt.date(2026, 10, 25)  # Sunday
    df = synthetic_day("2026-10-25", [50.0] * 8 + [10.0] * 4 + [20.0] * 4 + [50.0] * 84)
    rows = [{"Dia": "25/10/2026", "Hora": "xx", "PCB": f"{i}"} for i in range(25)]
    pv = pvpc.parse(pd.Series({"PVPC": rows}).to_json(), day)
    for tariff, period in (("2.0TD", "P3"), ("3.0TD", "P6")):
        est = retail.estimate(df, tariff, A, pv)
        assert len(est) == 100 and set(est["period"]) == {period}
        assert list(est["local_hour"][8:16]) == [2] * 8 and est["local_hour"][16] == 3
        assert list(est["local_start"][8:16]) == ["02:00", "02:15", "02:30", "02:45"] * 2
        assert list(est["pvpc_eur_kwh"][8:16]) == [0.002] * 4 + [0.003] * 4
        h = retail.hourly(est)
        assert len(h) == 25 and list(h["local_hour"][:5]) == [0, 1, 2, 2, 3]
        assert list(h["omie_eur_mwh"][2:4]) == [10.0, 20.0]
        w = retail.cheapest_window(est, 60)
        assert (w["start"], w["end"]) == ("02:00 CEST", "02:00 CET")


def test_hourly_labels_disambiguate_the_repeated_hour_on_the_100_period_fixture():
    est = retail.estimate(load_day("20251026"), "2.0TD", A, allow_other_year=True)  # 2025: other year
    assert len(est) == 100
    h = retail.hourly(est)
    assert list(h["hour"][:5]) == ["00:00", "01:00", "02:00 CEST", "02:00 CET", "03:00"]
    assert h["hour"].is_unique and len(h) == 25 and h["hour"].iloc[-1] == "23:00"
    records = retail.day_report(est, A)["hourly"]
    assert [(r["hour"], r["ts_utc"], r["local_hour"]) for r in records[2:4]] == [
        ("02:00 CEST", "2025-10-26T00:00:00Z", 2),
        ("02:00 CET", "2025-10-26T01:00:00Z", 2),
    ]
    assert len({r["ts_utc"] for r in records}) == 25
    assert retail.hourly_records(retail.estimate(load_day("20260329"), "2.0TD", A))[2]["hour"] == "03:00"


@pytest.mark.parametrize("minutes", [0, -60, 10, 50, 170, 181])
def test_window_length_must_be_a_positive_multiple_of_the_period(golden, minutes):
    est = retail.estimate(golden, "2.0TD", A)
    with pytest.raises(ValueError, match="not a positive multiple of the 15-minute period"):
        retail.cheapest_window(est, minutes)
    with pytest.raises(ValueError, match="not a positive multiple"):
        retail.compare_windows(est, minutes)


def test_window_length_ok_and_too_long(golden):
    est = retail.estimate(golden, "2.0TD", A)
    assert retail.cheapest_window(est, 15)["start"] == "15:00"
    assert retail.cheapest_window(est, 1440)["start"] == "00:00"
    with pytest.raises(ValueError, match="cannot fit"):
        retail.cheapest_window(est, 1455)
