from collections import deque

from app.services.profile_intelligence.capability_graph import (
    CAPABILITY_GRAPH,
)


class CapabilityInference:
    """
    Infers professional capabilities from explicit evidence.

    Example

    Financial Modelling
            ↓
    Business Valuation
            ↓
    Corporate Finance

    Each inference level slightly reduces confidence.
    """

    def __init__(self):

        self.graph = CAPABILITY_GRAPH

    # ==========================================================
    # Infer One Capability
    # ==========================================================

    def infer(self, capability):

        if not capability:

            return {}

        inferred = {}

        visited = set()

        queue = deque()

        queue.append((capability, 100))

        while queue:

            node, confidence = queue.popleft()

            if node in visited:

                continue

            visited.add(node)

            inferred[node] = max(

                inferred.get(node, 0),

                confidence

            )

            children = self.graph.get(

                node,

                []

            )

            for child in children:

                next_confidence = max(

                    confidence - 15,

                    40

                )

                queue.append(

                    (

                        child,

                        next_confidence

                    )

                )

        return inferred

    # ==========================================================
    # Infer Multiple Capabilities
    # ==========================================================

    def infer_all(self, capabilities):

        merged = {}

        for capability in capabilities:

            result = self.infer(

                capability

            )

            for item, confidence in result.items():

                merged[item] = max(

                    merged.get(item, 0),

                    confidence

                )

        return dict(

            sorted(

                merged.items(),

                key=lambda x: (

                    -x[1],

                    x[0]

                )

            )

        )

    # ==========================================================
    # Build Capability Records
    # ==========================================================

    def build_records(self, capabilities):

        inferred = self.infer_all(

            capabilities

        )

        records = []

        for capability, confidence in inferred.items():

            records.append(

                {

                    "capability": capability,

                    "confidence": confidence,

                    "explicit": confidence == 100,

                    "inferred": confidence < 100

                }

            )

        records.sort(

            key=lambda x: (

                -x["confidence"],

                x["capability"]

            )

        )

        return records