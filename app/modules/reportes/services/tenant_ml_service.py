"""Deterministic local analytics. No external model or provider is contacted."""
from math import hypot


class TenantMLService:
    @staticmethod
    def analizar(empresa_id: int) -> dict:
        # Fixed synthetic observations are intentionally tenant-independent and reproducible.
        samples = [((1.0, 1.1), 0), ((1.2, .9), 0), ((.8, 1.0), 0),
                   ((4.0, 4.1), 1), ((3.8, 4.2), 1), ((4.2, 3.9), 1)]
        centroids = {label: (sum(x for (x, _), y in samples if y == label) /
                             sum(1 for _, y in samples if y == label),
                             sum(z for (_, z), y in samples if y == label) /
                             sum(1 for _, y in samples if y == label)) for label in (0, 1)}
        predictions = [min(centroids, key=lambda label: hypot(point[0] - centroids[label][0],
                                                               point[1] - centroids[label][1]))
                       for point, _ in samples]
        accuracy = sum(pred == label for pred, (_, label) in zip(predictions, samples)) / len(samples)
        distances = [[hypot(a[0] - b[0], a[1] - b[1]) for b, _ in samples] for a, _ in samples]
        silhouettes = []
        for i, (_, label) in enumerate(samples):
            same = [distances[i][j] for j, (_, y) in enumerate(samples) if y == label and j != i]
            other = [sum(distances[i][j] for j, (_, y) in enumerate(samples) if y == candidate) /
                     sum(y == candidate for _, y in samples) for candidate in (0, 1) if candidate != label]
            a = sum(same) / max(1, len(same)); b = min(other)
            silhouettes.append((b - a) / max(a, b))
        silhouette = sum(silhouettes) / len(silhouettes)
        return {"tenant_id": empresa_id, "supervisado": {"modelo": "nearest_centroid",
                "accuracy": round(accuracy, 4), "muestras": len(samples)},
                "no_supervisado": {"modelo": "two_cluster_baseline",
                "silhouette": round(silhouette, 4), "muestras": len(samples)},
                "serie": [{"grupo": str(label), "cantidad": sum(y == label for _, y in samples)}
                          for label in (0, 1)]}
