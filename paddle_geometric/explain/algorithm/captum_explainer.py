import inspect
import logging
import warnings
from typing import Any, Dict, Optional, Union

import paddle
from paddle import Tensor

from paddle_geometric.explain import Explanation, HeteroExplanation
from paddle_geometric.explain.algorithm import ExplainerAlgorithm
from paddle_geometric.explain.algorithm.captum import (
    CaptumHeteroModel,
    CaptumModel,
    MaskLevelType,
    convert_captum_output,
    to_captum_input,
)
from paddle_geometric.explain.config import MaskType, ModelMode, ModelReturnType
from paddle_geometric.typing import EdgeType, NodeType


class CaptumExplainer(ExplainerAlgorithm):
    SUPPORTED_METHODS = [
        'IntegratedGradients',
        'Saliency',
        'InputXGradient',
        'Deconvolution',
        'ShapleyValueSampling',
        'GuidedBackprop',
    ]

    def __init__(
        self,
        attribution_method: Union[str, Any],
        **kwargs,
    ):
        super().__init__()

        import captum.attr

        if isinstance(attribution_method, str):
            self.attribution_method_class = getattr(
                captum.attr,
                attribution_method,
            )
        else:
            self.attribution_method_class = attribution_method

        if not self._is_supported_attribution_method():
            raise ValueError(f"{self.__class__.__name__} does not support "
                             f"attribution method "
                             f"{self.attribution_method_class.__name__}")

        if kwargs.get('internal_batch_size', 1) != 1:
            warnings.warn("Overriding 'internal_batch_size' to 1")

        if 'internal_batch_size' in self._get_attribute_parameters():
            kwargs['internal_batch_size'] = 1

        self.kwargs = kwargs

    def _get_mask_type(self) -> MaskLevelType:
        node_mask_type = self.explainer_config.node_mask_type
        edge_mask_type = self.explainer_config.edge_mask_type
        if node_mask_type is not None and edge_mask_type is not None:
            mask_type = MaskLevelType.node_and_edge
        elif node_mask_type is not None:
            mask_type = MaskLevelType.node
        elif edge_mask_type is not None:
            mask_type = MaskLevelType.edge
        else:
            raise ValueError("Neither node mask type nor "
                             "edge mask type is specified.")
        return mask_type

    def _get_attribute_parameters(self) -> Dict[str, Any]:
        signature = inspect.signature(self.attribution_method_class.attribute)
        return signature.parameters

    def _needs_baseline(self) -> bool:
        parameters = self._get_attribute_parameters()
        if 'baselines' in parameters:
            param = parameters['baselines']
            if param.default is inspect.Parameter.empty:
                return True
        return False

    def _is_supported_attribution_method(self) -> bool:
        if self._needs_baseline():
            return False
        elif self.attribution_method_class.__name__ in self.SUPPORTED_METHODS:
            return True
        return False

    def forward(
        self,
        model: paddle.nn.Layer,
        x: Union[Tensor, Dict[NodeType, Tensor]],
        edge_index: Union[Tensor, Dict[EdgeType, Tensor]],
        *,
        target: Tensor,
        index: Optional[Union[int, Tensor]] = None,
        **kwargs,
    ) -> Union[Explanation, HeteroExplanation]:

        mask_type = self._get_mask_type()

        inputs, add_forward_args = to_captum_input(
            x,
            edge_index,
            mask_type,
            *kwargs.values(),
        )

        if isinstance(x, dict):  # Heterogeneous GNN:
            metadata = (list(x.keys()), list(edge_index.keys()))
            captum_model = CaptumHeteroModel(
                model,
                mask_type,
                index,
                metadata,
                self.model_config,
            )
        else:  # Homogeneous GNN:
            metadata = None
            captum_model = CaptumModel(
                model,
                mask_type,
                index,
                self.model_config,
            )

        self.attribution_method_instance = self.attribution_method_class(
            captum_model)

        if self.model_config.mode == ModelMode.regression:
            target = None
        elif index is not None:
            target = target[index]

        attributions = self.attribution_method_instance.attribute(
            inputs=inputs,
            target=target,
            additional_forward_args=add_forward_args,
            **self.kwargs,
        )

        node_mask, edge_mask = convert_captum_output(
            attributions,
            mask_type,
            metadata,
        )

        if not isinstance(x, dict):
            return Explanation(node_mask=node_mask, edge_mask=edge_mask)

        explanation = HeteroExplanation()
        explanation.set_value_dict('node_mask', node_mask)
        explanation.set_value_dict('edge_mask', edge_mask)
        return explanation

    def supports(self) -> bool:
        node_mask_type = self.explainer_config.node_mask_type
        if node_mask_type not in [None, MaskType.attributes]:
            logging.error(f"'{self.__class__.__name__}' expects "
                          f"'node_mask_type' to be 'None' or 'attributes' "
                          f"(got '{node_mask_type.value}')")
            return False

        return_type = self.model_config.return_type
        if (self.model_config.mode == ModelMode.binary_classification
                and return_type != ModelReturnType.probs):
            logging.error(f"'{self.__class__.__name__}' expects "
                          f"'return_type' to be 'probs' for binary "
                          f"classification tasks (got '{return_type.value}')")
            return False

        return True
