"""Compare a transcribed public board with an SDK probe, without enabling finance."""
import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import sys


def positive(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("Expected positive finite number")
    return number


def displayed_volume(value):
    # Explicit transcription format, NOT a general Vietnamese decimal parser.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", value):
        raise ValueError("Volume transcription must contain grouped integer digits")
    return positive(value.replace(",", ""))


def compare_board(board, probe):
    if board.get("display_volume_format") != "comma_grouped_digits":
        raise ValueError("Unreviewed display format")
    if probe.get("version") != "5.2.1" or not probe.get("market_request_succeeded"):
        raise ValueError("Need a successful vnstock 5.2.1 probe")
    price_factor = positive(board["candidate_price_multiplier_to_vnd"])
    volume_factor = positive(board["candidate_volume_multiplier_to_shares"])
    sdk = {row["symbol"]: row for row in probe["rows"]}
    comparisons = []
    for row in board["rows"]:
        quote = sdk.get(row["symbol"], {})
        for side in ("bid", "ask"):
            for level, (price, volume) in enumerate(row[side+"s"], 1):
                scaled_price = positive(price) * price_factor
                candidate_shares = displayed_volume(volume) * volume_factor
                # A new better quote may shift all levels. Match by price AND side.
                candidates = [i for i in range(1, 4)
                              if quote.get(f"{side}_price_{i}") is not None
                              and Decimal(str(quote[f"{side}_price_{i}"])) == scaled_price]
                match = candidates[0] if len(candidates) == 1 else None
                sdk_volume = quote.get(f"{side}_vol_{match}") if match else None
                equal = sdk_volume is not None and Decimal(str(sdk_volume)) == candidate_shares
                comparisons.append({"symbol":row["symbol"], "side":side, "board_level":level,
                    "board_price_text":price, "board_volume_text":volume,
                    "candidate_price_vnd":str(scaled_price), "candidate_volume_shares":str(candidate_shares),
                    "sdk_level_at_same_price":match, "sdk_volume":sdk_volume,
                    "comparison":"EXACT_UNDER_CANDIDATE_FACTORS" if equal else
                        "QUANTITY_DIFFERS" if match else "PRICE_NOT_IN_SDK_TOP3"})
    exact = [c for c in comparisons if c["comparison"] == "EXACT_UNDER_CANDIDATE_FACTORS"]
    captured = board.get("captured_at_local")
    received = probe.get("received_at_utc")
    gap = None
    if captured and received:
        start, end = datetime.fromisoformat(captured), datetime.fromisoformat(received)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Evidence timestamps require timezone")
        gap = abs((end-start).total_seconds())
    return {"status":"NUMERIC_CORROBORATION_ONLY", "units_verified":False, "finance_enabled":False,
        "sdk_received_at_utc":received, "board_captured_at_local":captured, "capture_gap_seconds":gap,
        "board_capture_time_description":board.get("user_reported_capture_time"),
        "candidate_sdk_price_multiplier_to_vnd":"1", "candidate_sdk_volume_multiplier_to_shares":"1",
        "board_volume_multiplier_tested":str(volume_factor), "exact_pairs":len(exact),
        "total_board_pairs":len(comparisons), "symbols_with_exact_pairs":sorted({c["symbol"] for c in exact}),
        "comparisons":comparisons,
        "limitations":["Capture time and current board unit caption require independent review.",
            "Changing quotes are recorded as differences; no interpolation or correction.",
            "Candidate multipliers are hypotheses. This report never creates a verified contract or modifies SQLite."]}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    board, probe = (json.loads(Path(p).read_text(encoding="utf-8-sig")) for p in (args.board, args.probe))
    result = compare_board(board, probe)
    result.update(board_artifact=args.board, sdk_artifact=args.probe)
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("status", "exact_pairs", "total_board_pairs", "units_verified")}))


if __name__ == "__main__":
    main()
