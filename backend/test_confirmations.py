from app import is_positive_confirmation, is_negative_confirmation

# Test positive confirmations
positive_tests = ["yes", "yep", "yeah", "yup", "ok", "okay", "sure", "continue", "proceed", "book", "confirm", "YES PLEASE", "Yep!", "Yeah sure"]
print("=== POSITIVE CONFIRMATIONS ===")
for test in positive_tests:
    result = is_positive_confirmation(test)
    print(f"  '{test}' -> {result}")

# Test negative confirmations  
negative_tests = ["no", "nope", "cancel", "stop", "later", "NO WAY", "Nope!", "Cancel please"]
print("\n=== NEGATIVE CONFIRMATIONS ===")
for test in negative_tests:
    result = is_negative_confirmation(test)
    print(f"  '{test}' -> {result}")

# Test neutral inputs
neutral_tests = ["maybe", "i'll think about it", "what's the price", "hello"]
print("\n=== NEUTRAL INPUTS ===")
for test in neutral_tests:
    pos = is_positive_confirmation(test)
    neg = is_negative_confirmation(test)
    print(f"  '{test}' -> positive: {pos}, negative: {neg}")
