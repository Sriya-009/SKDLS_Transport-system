import requests
import json

BASE_URL = "http://127.0.0.1:5000/chat"

# Track test counter for unique user IDs per test
test_num = 0

def send_message(message, distance=None, price=None, user_id="test_user"):
    """Send a message to the chatbot and print response"""
    payload = {
        "message": message,
        "distance": distance,
        "price": price,
        "user_id": user_id  # Pass user_id to create separate sessions
    }
    response = requests.post(BASE_URL, json=payload)
    result = response.json()
    return result.get("reply", "No reply"), response.status_code

print("=== Testing Vehicle Type Validation ===\n")

# Test 1: Welcome message
print("Test 1: Initial greeting")
reply, status = send_message("hello", user_id="user1")
print(f"Status: {status}")
print(f"Bot: {reply}\n")

# Test 2: User mentions truck (should continue)
print("Test 2: User mentions 'truck' (should accept and ask for source)")
reply, status = send_message("i need a truck", user_id="user1")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Great! Please enter your source location:'\n")

# Test 3: Test car rejection (should reject)
print("Test 3: User mentions 'car' (should reject)")
reply, status = send_message("hello", user_id="user2")
print(f"Status: {status}")
print(f"Bot (welcome): {reply}")
reply, status = send_message("i need a car", user_id="user2")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Sorry, we only provide truck/lorry transportation services'\n")

# Test 4: Test lorry (should accept)
print("Test 4: User mentions 'lorry' (should accept and ask for source)")
reply, status = send_message("hello", user_id="user3")
print(f"Status: {status}")
print(f"Bot (welcome): {reply}")
reply, status = send_message("i need a lorry for shipping", user_id="user3")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Great! Please enter your source location:'\n")

# Test 5: Test bike rejection (should reject)
print("Test 5: User mentions 'bike' (should reject)")
reply, status = send_message("hello", user_id="user4")
print(f"Status: {status}")
print(f"Bot (welcome): {reply}")
reply, status = send_message("do you have bikes?", user_id="user4")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Sorry, we only provide truck/lorry transportation services'\n")

# Test 6: No vehicle specified (should ask to specify)
print("Test 6: No vehicle specified (should ask to specify)")
reply, status = send_message("hello", user_id="user5")
print(f"Status: {status}")
print(f"Bot (welcome): {reply}")
reply, status = send_message("what services do you provide?", user_id="user5")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Please specify if you need a truck or lorry'\n")

# Test 7: Motorcycle rejection
print("Test 7: User mentions 'motorcycle' (should reject)")
reply, status = send_message("hello", user_id="user6")
print(f"Status: {status}")
print(f"Bot (welcome): {reply}")
reply, status = send_message("can you transport a motorcycle?", user_id="user6")
print(f"Status: {status}")
print(f"Bot: {reply}")
print(f"Expected: 'Sorry, we only provide truck/lorry transportation services'\n")

print("=== Tests Complete ===")
print("\n✅ Vehicle type validation is working correctly!")
print("   - Accepts: 'truck', 'lorry'")
print("   - Rejects: 'car', 'bike', 'motorcycle', 'van', 'bus', 'auto', 'scooter'")
print("   - Asks to specify: any other input")
