# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import typing
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from maro.simulator.scenarios.supply_chain.actions import ConsumerAction
from maro.simulator.scenarios.supply_chain.datamodels import ConsumerDataModel
from maro.simulator.scenarios.supply_chain.order import Order

from .extendunitbase import ExtendUnitBase, ExtendUnitInfo
from .unitbase import UnitBase

if typing.TYPE_CHECKING:
    from maro.simulator.scenarios.supply_chain.facilities import FacilityBase
    from maro.simulator.scenarios.supply_chain.world import World


@dataclass
class ConsumerUnitInfo(ExtendUnitInfo):
    source_facility_id_list: List[int]


class ConsumerUnit(ExtendUnitBase):
    """Consumer unit used to generate order to purchase from upstream by action."""

    def __init__(
        self, id: int, data_model_name: Optional[str], data_model_index: Optional[int],
        facility: FacilityBase, parent: Union[FacilityBase, UnitBase], world: World, config: dict,
    ) -> None:
        super(ConsumerUnit, self).__init__(
            id, data_model_name, data_model_index, facility, parent, world, config,
        )
        self.order_quantity_on_the_way: Dict[int, int] = defaultdict(int)
        self.waiting_order_quantity: int = 0
        self._open_orders = Counter()
        self._in_transit_quantity: int = 0

        # States in python side.
        self._received: int = 0  # The quantity of product received in current step.

        self._purchased: int = 0  # The quantity of product that purchased from upstream.
        self._order_product_cost: float = 0  # order.quantity * upstream.price
        self._order_base_cost: float = 0  # order.quantity * unit_order_cost

        self.source_facility_id_list: List[int] = []

        self._unit_order_cost: float = 0

    def get_pending_order_daily(self, tick: int) -> List[int]:
        ret = [
            self.order_quantity_on_the_way[tick + i] for i in range(self.world.configs.settings["pending_order_len"])
        ]
        if tick in self.order_quantity_on_the_way:  # Remove data at tick to save storage
            self.order_quantity_on_the_way.pop(tick)
        return ret

    @property
    def in_transit_quantity(self) -> int:
        return self._in_transit_quantity

    def on_order_reception(self, order: Order, received_quantity: int, tick: int) -> None:
        """Called after order product is received.

        Args:
            order(Order): The order the products received belongs to.
            received_quantity (int): How many we received.
            tick (int): The simulation tick.
        """
        assert order.sku_id == self.sku_id
        self._received += received_quantity

        order.receive(tick, received_quantity)
        # TODO: add order related statistics

        self._update_open_orders(order.src_facility.id, order.sku_id, -received_quantity)

    def _update_open_orders(self, source_id: int, sku_id: int, additional_quantity: int) -> None:
        """Update the order states.

        Args:
            source_id (int): Where is the product from (facility id).
            sku_id (int): What product in the order.
            additional_quantity (int): Number of product to update (sum).
        """
        # New order for product.
        assert sku_id == self.sku_id
        self._open_orders[source_id] += additional_quantity
        self._in_transit_quantity += additional_quantity

    def initialize(self) -> None:
        super(ConsumerUnit, self).initialize()

        self._unit_order_cost = self.facility.skus[self.sku_id].unit_order_cost

        assert isinstance(self.data_model, ConsumerDataModel)

        self.data_model.initialize()

        self.source_facility_id_list = [
            source_facility.id for source_facility in self.facility.upstream_facility_list[self.sku_id]
        ]

    """
    ConsumerAction would be given after BE.step(), assume we take action at t0,
    the order would be placed to the DistributionUnit immediately (at t0)
    and would be scheduled if has available vehicle (at t0), assume in the case we have available vehicle,
    then the products would arrive at the destination at the end of (t0 + vlt) in the post_step(), which means
    at (t0 + vlt), these products can't be consumed to fulfill the demand from the downstreams/customer.
    """

    def process_actions(self, actions: List[ConsumerAction], tick: int) -> None:
        self._order_product_cost = self._order_base_cost = self._purchased = 0
        for action in actions:
            self._process_action(action, tick)

    def _process_action(self, action: ConsumerAction, tick: int) -> None:
        # NOTE: id == 0 means invalid, as our id is 1-based.
        if any([
            action.source_id not in self.source_facility_id_list,
            action.sku_id != self.sku_id,
            action.quantity <= 0,
        ]):
            return

        self._update_open_orders(action.source_id, action.sku_id, action.quantity)

        source_facility = self.world.get_facility_by_id(action.source_id)
        expected_vlt = source_facility.downstream_vlt_infos[self.sku_id][self.facility.id][action.vehicle_type].vlt

        order = Order(
            src_facility=source_facility,
            dest_facility=self.facility,
            sku_id=self.sku_id,
            quantity=action.quantity,
            vehicle_type=action.vehicle_type,
            creation_tick=tick,
            expected_finish_tick=tick + expected_vlt,
            expiration_buffer=action.expiration_buffer,
        )

        # Here the order cost is calculated by the upper distribution unit, with the sku price in that facility.
        self._order_product_cost += source_facility.distribution.place_order(order)
        # TODO: the order would be cancelled if there is no available vehicles,
        # TODO: but the cost is not decreased at that time.

        self._order_base_cost += order.quantity * self._unit_order_cost

        self._purchased += action.quantity

    def pre_step(self, tick: int) -> None:
        if self._received > 0:
            self._received = 0

            self.data_model.received = 0

        if self._purchased > 0:
            self._purchased = 0
            self._order_product_cost = 0
            self._order_base_cost = 0

            self.data_model.purchased = 0
            self.data_model.order_product_cost = 0
            self.data_model.order_base_cost = 0
            self.data_model.latest_consumptions = 0

    def step(self, tick: int) -> None:
        pass

    def flush_states(self) -> None:
        if self._received > 0:
            self.data_model.received = self._received

        if self._purchased > 0:
            self.data_model.purchased = self._purchased
            self.data_model.order_product_cost = self._order_product_cost
            self.data_model.order_base_cost = self._order_base_cost
            self.data_model.latest_consumptions = 1.0

        if self._received > 0 or self._purchased > 0:
            self.data_model.in_transit_quantity = self.in_transit_quantity

    def reset(self) -> None:
        super(ConsumerUnit, self).reset()

        self._open_orders.clear()
        self._in_transit_quantity = 0

        # Reset status in Python side.
        self._received = 0
        self._purchased = 0
        self._order_product_cost = 0
        self._order_base_cost = 0

    def get_unit_info(self) -> ConsumerUnitInfo:
        return ConsumerUnitInfo(
            **super(ConsumerUnit, self).get_unit_info().__dict__,
            source_facility_id_list=self.source_facility_id_list,
        )
