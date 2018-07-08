"""
This module is responsible for composing the search space.
"""
from typing import Iterator, Type
import logging

import darjeeling.transformation
from darjeeling.transformation import Transformation, \
                                      sample_by_localization_and_type
from darjeeling.candidate import all_single_edit_patches
from darjeeling.core import FileLine
from darjeeling.candidate import Candidate
from darjeeling.problem import Problem
from darjeeling.localization import Localization

from .donor import load_pool

logger = logging.getLogger(__name__)  # type: logging.Logger
logger.setLevel(logging.DEBUG)


def build_search_space(problem: Problem,
                       localization: Localization
                       ) -> Iterator[Candidate]:
    """
    Used to compose the sequence of patches that should be attempted.
    """
    schemas = [
        darjeeling.transformation.AndToOr,
        darjeeling.transformation.OrToAnd,
        darjeeling.transformation.LEToGT,
        darjeeling.transformation.GTToLE,
        darjeeling.transformation.GEToLT,
        darjeeling.transformation.LTToGE,
        darjeeling.transformation.EQToNEQ,
        darjeeling.transformation.NEQToEQ,
        darjeeling.transformation.PlusToMinus,
        darjeeling.transformation.MinusToPlus,
        darjeeling.transformation.MulToDiv,
        darjeeling.transformation.DivToMul,
        darjeeling.transformation.SignedToUnsigned,
        #darjeeling.transformation.InsertVoidFunctionCall,
        #darjeeling.transformation.InsertConditionalReturn,
        #darjeeling.transformation.InsertConditionalBreak,
        #darjeeling.transformation.ApplyTransformation
    ]  # type: Type[Transformation]
    logger.info("constructing search space")
    snippets = load_pool()
    transformations = sample_by_localization_and_type(problem,
                                                      snippets,
                                                      localization,
                                                      schemas)
    candidates = all_single_edit_patches(transformations)
    logger.info("constructed search space")
    return candidates
