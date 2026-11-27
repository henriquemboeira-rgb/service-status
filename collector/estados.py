# Ordem de gravidade: o pior status vence na cor geral
SEVERIDADE = {"ok": 0, "unknown": 1, "degraded": 2, "down": 3}


def pior(statuses):
    """Devolve o status mais grave da lista. Lista vazia conta como unknown."""
    return max(statuses, key=SEVERIDADE.__getitem__, default="unknown")
