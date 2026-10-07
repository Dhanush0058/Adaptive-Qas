"""Modular Traffic Classifier for mixed broadband network flows."""

from typing import Dict, Optional, Tuple
import time

from adaptive_qos.common.models import FlowMetrics, FlowConfig
from adaptive_qos.classification.models import (
    ClassificationCategory,
    FlowFeatures,
    FlowClassificationResult,
    DataSource,
)
from adaptive_qos.classification.features import FlowFeatureExtractor
from adaptive_qos.classification.rules import ClassificationRules


class TrafficClassifier:
    """
    Classifies network flows into broad traffic categories:
    - Gaming
    - Video Call
    - Video Streaming
    - Bulk Download
    - Interactive / Real-time
    - Background / Unknown

    Computes dynamically calculated confidence scores based on feature distances.
    Zero payload inspection or decryption.
    """

    MIN_CONFIDENCE_THRESHOLD = 0.35

    def __init__(self, data_source: DataSource = DataSource.SIMULATION):
        self.data_source = data_source
        self.feature_extractor = FlowFeatureExtractor()
        self.rules = ClassificationRules()

    def classify_metric(
        self,
        metric: FlowMetrics,
        flow_config: Optional[FlowConfig] = None,
        elapsed_sec: Optional[float] = None,
    ) -> FlowClassificationResult:
        """Extract features and classify a live or recorded FlowMetrics object."""
        features = self.feature_extractor.extract(metric, flow_config, elapsed_sec)
        return self.classify_features(features)

    def classify_features(self, features: FlowFeatures) -> FlowClassificationResult:
        """Classify pre-extracted FlowFeatures into a category with computed confidence."""
        # 1. Compute similarity scores across all candidate categories
        scores: Dict[str, float] = {}
        explanations: Dict[str, str] = {}

        gaming_score, gaming_exp = self.rules.score_gaming(features)
        scores[ClassificationCategory.GAMING.value] = gaming_score
        explanations[ClassificationCategory.GAMING.value] = gaming_exp

        vcall_score, vcall_exp = self.rules.score_video_call(features)
        scores[ClassificationCategory.VIDEO_CALL.value] = vcall_score
        explanations[ClassificationCategory.VIDEO_CALL.value] = vcall_exp

        stream_score, stream_exp = self.rules.score_video_streaming(features)
        scores[ClassificationCategory.VIDEO_STREAMING.value] = stream_score
        explanations[ClassificationCategory.VIDEO_STREAMING.value] = stream_exp

        bulk_score, bulk_exp = self.rules.score_bulk_download(features)
        scores[ClassificationCategory.BULK_DOWNLOAD.value] = bulk_score
        explanations[ClassificationCategory.BULK_DOWNLOAD.value] = bulk_exp

        interactive_score, inter_exp = self.rules.score_interactive(features)
        scores[ClassificationCategory.INTERACTIVE.value] = interactive_score
        explanations[ClassificationCategory.INTERACTIVE.value] = inter_exp

        # 2. Sort scores to find winner
        sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        winner_cat_str, top_score = sorted_scores[0]

        # Find true competing runner-up (excluding generic interactive parent if winner is gaming/vcall)
        competing_scores = [
            (c, s) for c, s in sorted_scores[1:]
            if not (winner_cat_str in ["gaming", "video_call"] and c == "interactive")
        ]
        runner_up_cat_str, runner_up_score = competing_scores[0] if competing_scores else ("unknown", 0.0)

        # 3. Dynamic Confidence Calculation
        if top_score < self.MIN_CONFIDENCE_THRESHOLD:
            predicted_category = ClassificationCategory.BACKGROUND_UNKNOWN
            confidence = max(0.1, round(top_score, 3))
            reason = f"Low matching score across all classes (max {top_score:.2f}); classified as unknown."
        else:
            predicted_category = ClassificationCategory(winner_cat_str)
            # Ambiguity margin penalty: if competing runner-up is very close, confidence decreases
            margin = (top_score - runner_up_score) / max(0.01, top_score)
            confidence = top_score * (0.70 + 0.30 * min(1.0, margin))
            confidence = max(0.2, min(0.99, confidence))
            reason = explanations[winner_cat_str]

        return FlowClassificationResult(
            flow_id=features.flow_id,
            classification=predicted_category,
            confidence=round(confidence, 3),
            category_scores=scores,
            features=features,
            timestamp=time.time(),
            data_source=self.data_source,
            reasoning=reason,
        )

    def set_data_source(self, data_source: DataSource) -> None:
        self.data_source = data_source
