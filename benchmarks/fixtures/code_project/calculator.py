def safe_divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("division by zero")
    return a / b
