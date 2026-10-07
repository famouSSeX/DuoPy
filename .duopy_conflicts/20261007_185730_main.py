# Добро пожаловать в DuoPy!
# Совместное написание и запуск Python кода через интернет в реальном времени.

def calculate_stats(numbers: list[int]) -> dict:
    return {
        "count": len(numbers),
        "total": sum(numbers),
        "average": sum(numbers) / len(numbers) if numbers else 0,
        "max": max(numbers) if numbers else None,
        "min": min(numbers) if numbers else None,
    }

if __name__ == "__main__":
    data = [14, 28, 42, 56, 70, 99]
    print(f"Исходные данные: {data}")
    stats = calculate_stats(data)
    for key, value in stats.items():
        print(f"  • {key.capitalize()}: {value}")
