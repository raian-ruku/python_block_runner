# %% Problem 1: Imports and Setup
import math
import random

print("Libraries imported successfully.")
print(f"Python math module: π = {math.pi:.6f}")

# %% Problem 2: Basic Arithmetic Operations
a = 42
b = 7

print(f"a = {a}, b = {b}")
print(f"a + b = {a + b}")
print(f"a - b = {a - b}")
print(f"a × b = {a * b}")
print(f"a / b = {a / b:.4f}")
print(f"a mod b = {a % b}")
print(f"a ^ b = {a ** b}")

# %% Problem 3: Working with Lists and Loops
data = [random.randint(1, 100) for _ in range(10)]
print(f"Random dataset: {data}")
print(f"Sum       : {sum(data)}")
print(f"Mean      : {sum(data) / len(data):.2f}")
print(f"Min       : {min(data)}")
print(f"Max       : {max(data)}")
print(f"Sorted    : {sorted(data)}")

# %% Problem 4: Functions and Recursion
def fibonacci(n):
    """Return the nth Fibonacci number."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

sequence = [fibonacci(i) for i in range(12)]
print(f"Fibonacci sequence (0–11): {sequence}")

# %% Problem 5: String Manipulation
words = ["PyBlockRunner", "automates", "Python", "block", "execution"]
sentence = " ".join(words)
print(f"Original: {sentence}")
print(f"Uppercase: {sentence.upper()}")
print(f"Word count: {len(words)}")
print(f"Reversed words: {' '.join(reversed(words))}")

# %% Problem 6: Dictionary and Comprehensions
squares = {n: n**2 for n in range(1, 11)}
print("Number squares:")
for k, v in squares.items():
    print(f"  {k:2d}² = {v:3d}")

# %% Problem 7: Exception Handling Demonstration
print("Testing exception handling...")
try:
    result = 10 / 0
except ZeroDivisionError as e:
    print(f"  Caught expected error: {e}")

try:
    items = [1, 2, 3]
    val = items[99]
except IndexError as e:
    print(f"  Caught expected error: {e}")

print("Exception handling works correctly.")
