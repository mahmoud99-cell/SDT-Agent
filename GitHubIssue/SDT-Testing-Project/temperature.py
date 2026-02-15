def celsius_to_fahrenheit(celsius: float) -> float:
    """
    Converts Celsius to Fahrenheit.

    Args:
        celsius: The temperature in Celsius.

    Returns:
        The temperature in Fahrenheit, rounded to 2 decimal places.

    Examples:
        >>> celsius_to_fahrenheit(0)
        32.0
        >>> celsius_to_fahrenheit(100)
        212.0
        >>> celsius_to_fahrenheit(-40)
        -40.0
        >>> celsius_to_fahrenheit(-273.15) # Absolute zero
        -459.67
    """
    fahrenheit = (celsius * 9/5) + 32
    return round(fahrenheit, 2)


def fahrenheit_to_celsius(fahrenheit: float) -> float:
    """
    Converts Fahrenheit to Celsius.

    Args:
        fahrenheit: The temperature in Fahrenheit.

    Returns:
        The temperature in Celsius, rounded to 2 decimal places.

    Examples:
        >>> fahrenheit_to_celsius(32)
        0.0
        >>> fahrenheit_to_celsius(212)
        100.0
        >>> fahrenheit_to_celsius(-40)
        -40.0
        >>> fahrenheit_to_celsius(-459.67) # Absolute zero
        -273.15
    """
    celsius = (fahrenheit - 32) * 5/9
    return round(celsius, 2)
