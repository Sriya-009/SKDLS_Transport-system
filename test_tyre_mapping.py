#!/usr/bin/env python3
"""Test the tyre recommendation mapping"""

def _get_recommended_tyre_type(tons):
    """Map load weight (tons) to recommended tyre type with logging.
    
    Business rules:
    - 20–25 tons → 12 tyre
    - 25–30 tons → 14 tyre
    - 30–35 tons → 16 tyre
    
    Returns: "12", "14", or "16" (tyre code), or "" if out of range
    """
    try:
        tons_value = int(float(tons))
    except (TypeError, ValueError):
        print(f"[booking][tyre_recommendation] invalid_input tons={tons!r} result=none")
        return ""

    if 20 <= tons_value < 25:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=12_tyre")
        return "12"
    elif 25 <= tons_value < 30:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=14_tyre")
        return "14"
    elif 30 <= tons_value <= 35:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=16_tyre")
        return "16"
    else:
        print(f"[booking][tyre_recommendation] tons={tons_value} result=out_of_range")
        return ""


if __name__ == "__main__":
    # Test cases from requirements
    test_cases = [
        (22, "12"),   # 22 -> 12 tyre
        (27, "14"),   # 27 -> 14 tyre
        (33, "16"),   # 33 -> 16 tyre
        (20, "12"),   # Edge case: 20 -> 12 tyre
        (24, "12"),   # Edge case: 24 -> 12 tyre
        (25, "14"),   # Edge case: 25 -> 14 tyre
        (29, "14"),   # Edge case: 29 -> 14 tyre
        (30, "16"),   # Edge case: 30 -> 16 tyre
        (35, "16"),   # Edge case: 35 -> 16 tyre
        (19, ""),     # Out of range below
        (36, ""),     # Out of range above
    ]

    print("Testing tyre recommendation mapping:")
    print("-" * 50)
    passed = 0
    failed = 0
    for tons, expected in test_cases:
        result = _get_recommended_tyre_type(tons)
        status = "✓ PASS" if result == expected else "✗ FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
        print(f"{status}: tons={tons} -> expected={expected}, got={result}")
    
    print("-" * 50)
    print(f"Summary: {passed} passed, {failed} failed out of {len(test_cases)} tests")
    exit(0 if failed == 0 else 1)
