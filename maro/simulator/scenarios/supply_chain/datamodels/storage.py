# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from maro.backends.backend import AttributeType
from maro.backends.frame import NodeAttribute, node

from .base import DataModelBase


@node("storage")
class StorageDataModel(DataModelBase):
    """Data model for storage unit."""
    remaining_space = NodeAttribute(AttributeType.UInt)
    capacity = NodeAttribute(AttributeType.UInt)

    # original is , used to save product and its number
    product_list = NodeAttribute(AttributeType.UInt, 1, is_list=True)
    product_number = NodeAttribute(AttributeType.UInt, 1, is_list=True)

    def __init__(self):
        super(StorageDataModel, self).__init__()

        self._capacity = 0
        self._remaining_space = None

    def initialize(self, capacity: int = 0, remaining_space: int = None):
        self._capacity = capacity
        self._remaining_space = remaining_space

        self.reset()

    def reset(self):
        super(StorageDataModel, self).reset()

        self.capacity = self._capacity

        if self._remaining_space is not None:
            self.remaining_space = self._remaining_space
        else:
            self.remaining_space = self._capacity
