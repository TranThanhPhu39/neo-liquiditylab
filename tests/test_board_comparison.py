import pytest

from scripts.compare_kbs_board import compare_board, displayed_volume


def test_transcribed_format_does_not_treat_comma_as_decimal():
    assert displayed_volume("2,00") == 200
    assert displayed_volume("50,40") == 5040
    assert displayed_volume("10") == 10
    for text in ("2.00", "ATC", "-10", "NaN", "1,,00"):
        with pytest.raises(ValueError):
            displayed_volume(text)


def test_price_matching_handles_level_shift_and_never_verifies_units():
    board = {"display_volume_format":"comma_grouped_digits", "candidate_price_multiplier_to_vnd":"1000",
             "candidate_volume_multiplier_to_shares":"10", "captured_at_local":None,
             "rows":[{"symbol":"TEST", "bids":[["20.5","1,00"]], "asks":[["21","2,00"]]}]}
    probe = {"version":"5.2.1", "market_request_succeeded":True, "received_at_utc":"2026-10-28T07:34:00+00:00",
             "rows":[{"symbol":"TEST", "bid_price_1":20600, "bid_vol_1":300,
                      "bid_price_2":20500, "bid_vol_2":1000, "ask_price_1":21000, "ask_vol_1":2500}]}
    result = compare_board(board, probe)
    assert result["exact_pairs"] == 1 and result["total_board_pairs"] == 2
    assert result["comparisons"][0]["sdk_level_at_same_price"] == 2
    assert result["comparisons"][1]["comparison"] == "QUANTITY_DIFFERS"
    assert not result["units_verified"] and not result["finance_enabled"]
    assert result["capture_gap_seconds"] is None
    board["captured_at_local"] = "2026-10-28T14:33:00+07:00"
    assert compare_board(board, probe)["capture_gap_seconds"] == 60


def test_wrong_sdk_or_display_format_cannot_be_compared():
    with pytest.raises(ValueError):
        compare_board({"display_volume_format":"decimal_comma"}, {})
    with pytest.raises(ValueError):
        compare_board({"display_volume_format":"comma_grouped_digits"}, {"version":"5.0.1"})
