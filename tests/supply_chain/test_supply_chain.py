import os
import unittest
from typing import Dict, Optional

import numpy as np

from maro.simulator import Env
from maro.simulator.scenarios.supply_chain import (
    ConsumerAction, ConsumerUnit, DistributionUnit, FacilityBase, ManufactureAction, SellerUnit, StorageUnit,
    VehicleUnit,
)
from maro.simulator.scenarios.supply_chain.business_engine import SupplyChainBusinessEngine
from maro.simulator.scenarios.supply_chain.facilities.facility import FacilityInfo
from maro.simulator.scenarios.supply_chain.order import Order
from maro.simulator.scenarios.supply_chain.units.storage import AddStrategy


def build_env(case_name: str, durations: int):
    case_folder = os.path.join("tests", "data", "supply_chain", case_name)

    env = Env(scenario="supply_chain", topology=case_folder, durations=durations)

    return env


def get_product_dict_from_storage(env: Env, frame_index: int, node_index: int):
    product_list = env.snapshot_list["storage"][frame_index:node_index:"product_list"].flatten().astype(np.int)
    product_quantity = env.snapshot_list["storage"][frame_index:node_index:"product_quantity"].flatten().astype(np.int)

    return {product_id: quantity for product_id, quantity in zip(product_list, product_quantity)}


SKU1_ID = 1
SKU2_ID = 2
SKU3_ID = 3
SKU4_ID = 4


class MyTestCase(unittest.TestCase):
    """
    manufacture unit testing:

    1. with input sku
        . meet the storage limitation
        . not meet the storage limitation
        . with enough source sku
        . without enough source sku
        . with product rate
        . without product rate
    2. without input sku
        . meet the storage limitation
        . not meet the storage limitation
        . with product rate
        . without product rate

    """

    def test_manufacture_meet_storage_limitation(self):
        """Test sku3 manufacturing. -- Supplier_SKU3"""
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        storage_nodes = env.snapshot_list["storage"]

        manufacture_nodes = env.snapshot_list["manufacture"]
        manufacture_number = len(manufacture_nodes)
        manufacture_features = (
            "id", "facility_id", "manufacture_quantity", "product_id"
        )
        IDX_ID, IDX_FACILITY_ID, IDX_MANUFACTURE_QUANTITY, IDX_PRODUCT_ID = 0, 1, 2, 3

        # ############################### TICK: 0 ######################################

        # tick 0 passed, no product manufacturing.
        env.step(None)

        states = manufacture_nodes[
            env.frame_index::manufacture_features
        ].flatten().reshape(manufacture_number, -1).astype(np.int)

        # try to find which one is sku3 manufacture unit.
        sku3_data_model_index: Optional[int] = None
        sku3_manufacture_id: Optional[int] = None
        sku3_facility_id: Optional[int] = None
        for index, state in enumerate(states):
            # Id of sku3 is 3.
            if state[IDX_PRODUCT_ID] == SKU3_ID:
                sku3_data_model_index = index
                sku3_manufacture_id = state[IDX_ID]
                sku3_facility_id = state[IDX_FACILITY_ID]
        self.assertTrue(all([
            sku3_data_model_index is not None,
            sku3_manufacture_id is not None,
            sku3_facility_id is not None
        ]))

        # try to find sku3's storage from env.summary
        sku3_facility_info: FacilityInfo = env.summary["node_mapping"]["facilities"][sku3_facility_id]
        sku3_storage_index = sku3_facility_info.storage_info.node_index

        capacities = storage_nodes[env.frame_index:sku3_storage_index:"capacity"].flatten().astype(np.int)
        remaining_spaces = storage_nodes[env.frame_index:sku3_storage_index:"remaining_space"].flatten().astype(np.int)

        # there should be 80 units been taken at the beginning according to the config file.
        # so remaining space should be 20
        self.assertEqual(20, remaining_spaces.sum())
        # capacity is 100 by config
        self.assertEqual(100, capacities.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku3_storage_index)

        # The product quantity should be same as configuration at beginning.
        # 80 sku3
        self.assertEqual(80, product_dict[SKU3_ID])

        # all the id is greater than 0
        self.assertGreater(sku3_manufacture_id, 0)

        # ############################### TICK: 1 ######################################

        # pass an action to start manufacturing for this tick.
        action = ManufactureAction(sku3_manufacture_id, 1)

        env.step([action])

        states = manufacture_nodes[env.frame_index:sku3_data_model_index:manufacture_features].flatten().astype(np.int)

        # Sku3 produce rate is 1 per tick, so manufacture_quantity should be 1.
        self.assertEqual(1, states[IDX_MANUFACTURE_QUANTITY])

        remaining_spaces = storage_nodes[env.frame_index:sku3_storage_index:"remaining_space"].flatten().astype(np.int)

        # now remaining space should be 20 - 1 = 19
        self.assertEqual(20 - 1, remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku3_storage_index)

        # sku3 quantity should be 80 + 1
        self.assertEqual(80 + 1, product_dict[SKU3_ID])

        # ############################### TICK: 2 ######################################

        # leave the action as None will cause manufacture unit stop manufacturing.
        env.step(None)

        states = manufacture_nodes[env.frame_index:sku3_data_model_index:manufacture_features].flatten().astype(np.int)

        # so manufacture_quantity should be 0
        self.assertEqual(0, states[IDX_MANUFACTURE_QUANTITY])

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku3_storage_index)

        # sku3 quantity should be same as last tick
        self.assertEqual(80 + 1, product_dict[SKU3_ID])

        # ############################### TICK: 3 ######################################

        # let is generate 20, but actually it can only procedure 19 because the storage will reach the limitation
        env.step([ManufactureAction(sku3_manufacture_id, 20)])

        states = manufacture_nodes[env.frame_index:sku3_data_model_index:manufacture_features].flatten().astype(np.int)

        # so manufacture_number should be 19 instead 20
        self.assertEqual(19, states[IDX_MANUFACTURE_QUANTITY])

        remaining_spaces = storage_nodes[env.frame_index:sku3_storage_index:"remaining_space"].flatten().astype(np.int)

        # now remaining space should be 0
        self.assertEqual(0, remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku3_storage_index)

        # sku3 quantity should be 100
        self.assertEqual(80 + 1 + 19, product_dict[SKU3_ID])

    def test_manufacture_meet_source_lack(self):
        """Test sku4 manufacturing. -- Supplier_SKU4.
        This sku supplier does not have enough source material at the begining,
        so it cannot produce anything without consumer purchase."""
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        storage_nodes = env.snapshot_list["storage"]

        manufacture_nodes = env.snapshot_list["manufacture"]
        manufacture_number = len(manufacture_nodes)
        manufacture_features = (
            "id", "facility_id", "manufacture_quantity", "product_id", "unit_product_cost"
        )
        IDX_ID, IDX_FACILITY_ID, IDX_MANUFACTURE_QUANTITY, IDX_PRODUCT_ID, IDX_UNIT_PRODUCT_COST = 0, 1, 2, 3, 4

        # ############################### TICK: 0 ######################################

        # tick 0 passed, no product manufacturing.
        env.step(None)

        states = manufacture_nodes[
            env.frame_index::manufacture_features
        ].flatten().reshape(manufacture_number, -1).astype(np.int)

        # try to find which one is sku3 manufacture unit.
        sku4_data_model_index: Optional[int] = None
        sku4_manufacture_id: Optional[int] = None
        sku4_facility_id: Optional[int] = None
        for index, state in enumerate(states):
            # Id of sku4 is 4.
            if state[IDX_PRODUCT_ID] == SKU4_ID:
                sku4_data_model_index = index
                sku4_manufacture_id = state[IDX_ID]
                sku4_facility_id = state[IDX_FACILITY_ID]
        self.assertTrue(all([
            sku4_data_model_index is not None,
            sku4_manufacture_id is not None,
            sku4_facility_id is not None
        ]))

        # try to find sku4's storage from env.summary
        sku4_facility_info: FacilityInfo = env.summary["node_mapping"]["facilities"][sku4_facility_id]
        sku4_storage_index = sku4_facility_info.storage_info.node_index

        # capacity is same as configured.
        capacities = storage_nodes[env.frame_index:sku4_storage_index:"capacity"].flatten().astype(np.int)
        self.assertEqual(200, capacities.sum())

        # remaining space should be capacity 200 - (sku4 50 + sku2 0)
        remaining_spaces = storage_nodes[env.frame_index:sku4_storage_index:"remaining_space"].flatten().astype(np.int)
        self.assertEqual(200 - (50 + 0), remaining_spaces.sum())

        # no manufacture number as we have not pass any action
        manufacture_states = manufacture_nodes[
            env.frame_index:sku4_data_model_index:manufacture_features
        ].flatten().astype(np.int)

        # manufacture_quantity should be 0
        self.assertEqual(0, manufacture_states[IDX_MANUFACTURE_QUANTITY])

        # output product id should be same as configured.
        self.assertEqual(4, manufacture_states[IDX_PRODUCT_ID])

        # product unit cost should be same as configured.
        self.assertEqual(4, manufacture_states[IDX_UNIT_PRODUCT_COST])

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku4_storage_index)

        # 50 sku4 at beginning
        self.assertEqual(50, product_dict[SKU4_ID])

        # 0 sku2
        self.assertEqual(0, product_dict[SKU2_ID])

        # ############################### TICK: 1 - end ######################################

        is_done = False

        while not is_done:
            # push to the end, the storage should not changed, no matter what production rate we give it.
            _, _, is_done = env.step([ManufactureAction(sku4_manufacture_id, 10)])

        manufacture_states = manufacture_nodes[
            env.frame_index:sku4_data_model_index:manufacture_features
        ].flatten().astype(np.int)

        # manufacture_quantity should be 0
        self.assertEqual(0, manufacture_states[IDX_MANUFACTURE_QUANTITY])

        # output product id should be same as configured.
        self.assertEqual(SKU4_ID, manufacture_states[IDX_PRODUCT_ID])

        # product unit cost should be same as configured.
        self.assertEqual(4, manufacture_states[IDX_UNIT_PRODUCT_COST])

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku4_storage_index)

        # 50 sku4 at beginning
        self.assertEqual(50, product_dict[SKU4_ID])

        # 0 sku2
        self.assertEqual(0, product_dict[SKU2_ID])

    def test_manufacture_meet_avg_storage_limitation(self):
        """Test on sku1 -- Supplier_SKU1.
        It is configured with nearly full initial states."""

        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        storage_nodes = env.snapshot_list["storage"]

        manufacture_nodes = env.snapshot_list["manufacture"]
        manufacture_number = len(manufacture_nodes)
        manufacture_features = (
            "id", "facility_id", "manufacture_quantity", "product_id", "unit_product_cost"
        )
        IDX_ID, IDX_FACILITY_ID, IDX_MANUFACTURE_QUANTITY, IDX_PRODUCT_ID, IDX_UNIT_PRODUCT_COST = 0, 1, 2, 3, 4

        # ############################### TICK: 0 ######################################

        # tick 0 passed, no product manufacturing, verified in above case, pass checking it here.
        env.step(None)

        states = manufacture_nodes[
            env.frame_index::manufacture_features
        ].flatten().reshape(manufacture_number, -1).astype(np.int)
        # try to find which one is sku3 manufacture unit.
        sku1_data_model_index: Optional[int] = None
        sku1_manufacture_id: Optional[int] = None
        sku1_facility_id: Optional[int] = None
        for index, state in enumerate(states):
            # Id of sku1 is 1.
            if state[IDX_PRODUCT_ID] == SKU1_ID:
                sku1_data_model_index = index
                sku1_manufacture_id = state[IDX_ID]
                sku1_facility_id = state[IDX_FACILITY_ID]
        self.assertTrue(all([
            sku1_data_model_index is not None,
            sku1_manufacture_id is not None,
            sku1_facility_id is not None
        ]))

        sku1_facility_info: FacilityInfo = env.summary["node_mapping"]["facilities"][sku1_facility_id]
        sku1_storage_index = sku1_facility_info.storage_info.node_index

        # ############################### TICK: 1 ######################################

        # ask sku1 manufacture start manufacturing, rate is 10.
        env.step([ManufactureAction(sku1_manufacture_id, 10)])

        manufacture_states = manufacture_nodes[
            env.frame_index:sku1_data_model_index:manufacture_features
        ].flatten().astype(np.int)

        # we can produce 4 sku1, as it will meet storage avg limitation per sku. 4 = 200//2 - 96
        self.assertEqual(200 // 2 - 96, manufacture_states[IDX_MANUFACTURE_QUANTITY])

        # so storage remaining space should be 200 - ((96 + 4) + (100 - 4 * 2 sku3/sku1))
        remaining_spaces = storage_nodes[
            env.frame_index:sku1_data_model_index:"remaining_space"
        ].flatten().astype(np.int)
        self.assertEqual(200 - ((96 + 4) + (100 - 4 * 2)), remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku1_storage_index)

        # The product quantity of sku1 should 100, just reach the avg storage capacity limitation
        self.assertEqual(200 // 2, product_dict[SKU1_ID])

        # 4 sku1 cost 4*2 source material (sku3)
        self.assertEqual(100 - 4 * 2, product_dict[SKU3_ID])

        # ############################### TICK: 1 ######################################

        # then fix the product rate to 20 every tick, but the manufacture will do nothing, as we have no enough space

        is_done = False

        while not is_done:
            _, _, is_done = env.step([ManufactureAction(sku1_storage_index, 20)])

        manufacture_states = manufacture_nodes[
            env.frame_index:sku1_data_model_index:manufacture_features
        ].flatten().astype(np.int)

        # but manufacture number is 0
        self.assertEqual(0, manufacture_states[IDX_MANUFACTURE_QUANTITY])

        # so storage remaining space should be 200 - ((96 + 4) + (100 - 4*2))
        remaining_spaces = storage_nodes[
            env.frame_index:sku1_storage_index:"remaining_space"
        ].flatten().astype(np.int)
        self.assertEqual(200 - ((96 + 4) + (100 - 4 * 2)), remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, sku1_storage_index)

        # The product quantity of sku1 should 100, just reach the avg storage capacity limitation
        self.assertEqual(100, product_dict[SKU1_ID])

        # 4 sku1 cost 4*2 source material (sku3)
        self.assertEqual(100 - 4 * 2, product_dict[SKU3_ID])

    # TODO: Add testing for SimpleManufactureUnit

    """
    Storage test:

    . take available
        . enough
        . not enough
    . try add products
        . meet whole storage capacity limitation
            . fail if all
            . not fail if all
        . enough space
    . try take products
        . have enough
        . not enough
    . get product quantity

    """

    # TODO: Add testing for given storage upper bound

    def test_storage_get_product_quantity_and_capacity_and_remaining_space(self):
        """Supplier_SKU1"""
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        env.step(None)

        supplier_1: FacilityBase = be.world._get_facility_by_name("Supplier_SKU1")
        storage_unit: StorageUnit = supplier_1.storage
        storage_node_index = storage_unit.data_model_index

        storage_nodes = env.snapshot_list["storage"]

        # ######################### Product Quantity ###########################
        init_product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)
        self.assertEqual(2, len(init_product_dict))

        # Inside StorageUnit
        self.assertEqual(96, storage_unit._product_level[SKU1_ID])
        self.assertEqual(100, storage_unit._product_level[SKU3_ID])
        # In Snapshot
        self.assertEqual(96, init_product_dict[SKU1_ID])
        self.assertEqual(100, init_product_dict[SKU3_ID])

        # ######################### Capacity ###########################
        capacities = storage_nodes[env.frame_index:storage_node_index:"capacity"].flatten().astype(np.int)
        self.assertEqual(200, storage_unit.capacity)
        self.assertEqual(200, capacities.sum())

        # ######################### Remaining Space ###########################
        init_remaining_spaces = storage_nodes[
            env.frame_index:storage_node_index:"remaining_space"
        ].flatten().astype(np.int)
        self.assertEqual(200 - 96 - 100, storage_unit.remaining_space)
        self.assertEqual(200 - 96 - 100, init_remaining_spaces.sum())

        # ######################### Remaining Space ###########################
        # Should not change even after reset
        env.reset()
        env.step(None)

        # ######################### Product Quantity ###########################
        init_product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)

        # Inside StorageUnit
        self.assertEqual(96, storage_unit._product_level[SKU1_ID])
        self.assertEqual(100, storage_unit._product_level[SKU3_ID])
        # In Snapshot
        self.assertEqual(96, init_product_dict[SKU1_ID])
        self.assertEqual(100, init_product_dict[SKU3_ID])

        # ######################### Capacity ###########################
        capacities = storage_nodes[env.frame_index:storage_node_index:"capacity"].flatten().astype(np.int)
        self.assertEqual(200, storage_unit.capacity)
        self.assertEqual(200, capacities.sum())

        # ######################### Remaining Space ###########################
        init_remaining_spaces = storage_nodes[
            env.frame_index:storage_node_index:"remaining_space"
        ].flatten().astype(np.int)
        self.assertEqual(200 - 96 - 100, storage_unit.remaining_space)
        self.assertEqual(200 - 96 - 100, init_remaining_spaces.sum())

    def test_storage_take_available(self):
        """Facility with single SKU. -- Supplier_SKU3"""
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        env.step(None)

        supplier_3: FacilityBase = be.world._get_facility_by_name("Supplier_SKU3")
        storage_unit: StorageUnit = supplier_3.storage
        storage_node_index = storage_unit.data_model_index

        # ######################### Take a right amount of quantity ##############################
        # Call take_available to take 40 sku3 in storage.
        actual_quantity = storage_unit.take_available(SKU3_ID, 40)
        self.assertEqual(40, actual_quantity)

        # Check if remaining quantity correct
        self.assertEqual(80 - 40, storage_unit._product_level[SKU3_ID])

        # call env.step will cause states write into snapshot
        env.step(None)

        # Check if the snapshot status correct
        product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)
        self.assertEqual(80 - 40, product_dict[SKU3_ID])

        # ######################### Take more than existing ##############################
        try_taken_quantity = (80 - 40) + 10
        actual_quantity = storage_unit.take_available(SKU3_ID, try_taken_quantity)
        # We should get all available
        self.assertEqual(actual_quantity, try_taken_quantity - 10)

        # take snapshot
        env.step(None)

        product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)

        # The product quantity should be 0, as we took all available
        self.assertEqual(0, product_dict[SKU3_ID])

    def test_storage_try_add_products(self):
        """Facility with multiple SKUs -- Supplier_SKU2
        NOTE:
            try_add_products method do not check avg storage capacity checking, so we will ignore it here.

        """
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        env.step(None)

        supplier_2 = be.world._get_facility_by_name("Supplier_SKU2")
        storage_unit = supplier_2.storage
        storage_node_index = storage_unit.data_model_index

        storage_nodes = env.snapshot_list["storage"]

        capacities = storage_nodes[env.frame_index:storage_node_index:"capacity"].flatten().astype(np.int)

        init_remaining_spaces = storage_nodes[
            env.frame_index:storage_node_index:"remaining_space"
        ].flatten().astype(np.int)

        init_product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)

        # ############################### IgnoreUpperBound AllOrNothing ######################################
        # 100 // 2 = 50
        avg_max_product_quantity = init_remaining_spaces.sum() // len(init_product_dict)
        self.assertEqual(50, avg_max_product_quantity)

        products_to_put = {
            SKU1_ID: 50 + 1,
            SKU2_ID: 50 + 1,
        }

        result = storage_unit.try_add_products(
            products_to_put,
            add_strategy=AddStrategy.IgnoreUpperBoundAllOrNothing
        )
        # the method will return an empty dictionary if fail to add
        self.assertEqual(0, len(result))

        # so remaining space should not change
        self.assertEqual(100, storage_unit.remaining_space)

        # each product quantity should be same as before
        self.assertEqual(50, storage_unit._product_level[SKU2_ID])
        self.assertEqual(50, storage_unit._product_level[SKU1_ID])

        # ############################### IgnoreUpperBound AddInOrder ######################################
        # Part of the product will be added to storage, and cause remaining space being 0
        result = storage_unit.try_add_products(
            products_to_put,
            add_strategy=AddStrategy.IgnoreUpperBoundAddInOrder
        )
        # all sku1 would be added successfully
        self.assertEqual(50 + (50 + 1), storage_unit._product_level[SKU1_ID])
        self.assertEqual(50 + (100 - (50 + 1)), storage_unit._product_level[SKU2_ID])

        self.assertEqual(0, storage_unit.remaining_space)

        # take snapshot
        env.step(None)

        # remaining space in snapshot should be 0
        remaining_spaces = storage_nodes[env.frame_index:storage_node_index:"remaining_space"].flatten().astype(np.int)
        self.assertEqual(0, remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)
        self.assertEqual(50 + 51, product_dict[SKU1_ID])
        self.assertEqual(50 + 49, product_dict[SKU2_ID])

        # total product quantity should be same as capacity
        self.assertEqual(capacities.sum(), sum(product_dict.values()))

        # ######################################################################
        # reset the env for next case
        env.reset()

        # check the state after reset
        self.assertEqual(capacities.sum(), storage_unit.capacity)
        self.assertEqual(init_remaining_spaces.sum(), storage_unit.remaining_space)

        for product_id, product_quantity in init_product_dict.items():
            self.assertEqual(product_quantity, storage_unit._product_level[product_id])

        # ############################### IgnoreUpperBound Proportional ######################################
        products_to_put = {
            SKU1_ID: 50,
            SKU2_ID: 150,
        }
        # Part of the product will be added to storage, and cause remaining space being 0
        result = storage_unit.try_add_products(
            products_to_put,
            add_strategy=AddStrategy.IgnoreUpperBoundProportional
        )
        # Only 100 // (50 + 150) = 1/2 incoming products can be added.
        self.assertEqual(50 + 50 // 2, storage_unit._product_level[SKU1_ID])
        self.assertEqual(50 + 150 // 2, storage_unit._product_level[SKU2_ID])

        self.assertEqual(0, storage_unit.remaining_space)

        # take snapshot
        env.step(None)

        # remaining space in snapshot should be 0
        remaining_spaces = storage_nodes[env.frame_index:storage_node_index:"remaining_space"].flatten().astype(np.int)
        self.assertEqual(0, remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)
        self.assertEqual(50 + 25, product_dict[SKU1_ID])
        self.assertEqual(50 + 75, product_dict[SKU2_ID])

        # ######################################################################
        # reset the env for next case
        env.reset()

        # check the state after reset
        self.assertEqual(capacities.sum(), storage_unit.capacity)
        self.assertEqual(init_remaining_spaces.sum(), storage_unit.remaining_space)

        for product_id, product_quantity in init_product_dict.items():
            self.assertEqual(product_quantity, storage_unit._product_level[product_id])

        # ############################### LimitedByUpperBound ######################################
        products_to_put = {
            SKU1_ID: 60,
            SKU2_ID: 40,
        }

        result = storage_unit.try_add_products(
            products_to_put,
            add_strategy=AddStrategy.LimitedByUpperBound
        )
        # the default upper bound is the avg capacity, so it would be 100 for both sku1 and sku2
        self.assertEqual(50 + min(100 - 50, 60), storage_unit._product_level[SKU1_ID])
        self.assertEqual(50 + min(100 - 50, 40), storage_unit._product_level[SKU2_ID])

        # 10 = 200 - (50 + 50) - (50 + 40)
        self.assertEqual(10, storage_unit.remaining_space)

        # take snapshot
        env.step(None)

        remaining_spaces = storage_nodes[env.frame_index:storage_node_index:"remaining_space"].flatten().astype(np.int)
        self.assertEqual(10, remaining_spaces.sum())

        product_dict = get_product_dict_from_storage(env, env.frame_index, storage_node_index)
        self.assertEqual(50 + 50, product_dict[SKU1_ID])
        self.assertEqual(50 + 40, product_dict[SKU2_ID])

    def test_storage_try_take_products(self):
        """Facility with single SKU. -- Supplier_SKU3"""
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        env.step(None)

        supplier_3: FacilityBase = be.world._get_facility_by_name("Supplier_SKU3")
        storage_unit: StorageUnit = supplier_3.storage
        storage_node_index = storage_unit.data_model_index

        storage_nodes = env.snapshot_list["storage"]

        # ############################### Take more than existing ######################################
        product_to_take = {
            SKU3_ID: 81,
        }
        # which this setting, it will return false, as no enough product for ous
        self.assertFalse(storage_unit.try_take_products(product_to_take))

        # so remaining space and product quantity should same as before
        self.assertEqual(100 - 80, storage_unit.remaining_space)
        self.assertEqual(80, storage_unit._product_level[SKU3_ID])

        # ############################### Take all ######################################
        # try to get all products
        product_to_take = {
            SKU3_ID: 80,
        }
        self.assertTrue(storage_unit.try_take_products(product_to_take))

        # now the remaining space should be same as capacity as we take all
        self.assertEqual(100, storage_unit.remaining_space)

        # take snapshot
        env.step(None)

        capacities = storage_nodes[env.frame_index:storage_node_index:"capacity"].flatten().astype(np.int)
        remaining_spaces = storage_nodes[env.frame_index:storage_node_index:"remaining_space"].flatten().astype(np.int)

        # remaining space should be same as capacity in snapshot
        self.assertEqual(capacities.sum(), remaining_spaces.sum())

    """

    Consumer test:

    . initial state
    . state after reset
    . set_action directly from code
    . set_action by env.step
    . call on_order_reception directly to simulation order arrived
    . call update_open_orders directly

    """

    def test_consumer_init_state(self):
        """
        NOTE: we will use consumer on Supplier_SKU1, as it contains a source for sku3 (Supplier_SKU3)
        """
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # print(env.summary)
        # we can get the consumer from env.summary

        # NOTE: though we are test with sku1, but the consumer is for sku3, as it is the source material from source
        sku3_consumer_unit: Optional[ConsumerUnit] = None
        sku3_product_unit_id: Optional[int] = None
        sku3_consumer_unit_id: Optional[int] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for facility_info in facility_info_dict.values():
            if facility_info.name == "Supplier_SKU1":
                # try to find sku3 consumer
                sku3_consumer_unit_id = facility_info.products_info[SKU3_ID].consumer_info.id

                sku3_consumer_unit = be.world.get_entity_by_id(sku3_consumer_unit_id)
                sku3_product_unit_id = facility_info.products_info[SKU3_ID].id

        sku3_consumer_data_model_index = env.summary["node_mapping"]["unit_mapping"][sku3_consumer_unit_id][1]

        # check initial state
        self.assertEqual(0, sku3_consumer_unit._received)
        self.assertEqual(0, sku3_consumer_unit._purchased)
        self.assertEqual(0, sku3_consumer_unit._order_product_cost)
        self.assertEqual(SKU3_ID, sku3_consumer_unit.product_id)

        # check data model state
        # order cost from configuration
        self.assertEqual(200, sku3_consumer_unit.data_model.order_cost)

        # NOTE: 0 is an invalid(initial) id
        self.assertEqual(SKU3_ID, sku3_consumer_unit.data_model.product_id)
        self.assertEqual(sku3_consumer_unit_id, sku3_consumer_unit.data_model.id)
        self.assertEqual(sku3_product_unit_id, sku3_consumer_unit.data_model.product_unit_id)
        self.assertEqual(0, sku3_consumer_unit.data_model.purchased)
        self.assertEqual(0, sku3_consumer_unit.data_model.received)
        self.assertEqual(0, sku3_consumer_unit.data_model.order_product_cost)

        # check sources
        for source_facility_id in sku3_consumer_unit.source_facility_id_list:
            source_facility: FacilityBase = be.world.get_facility_by_id(source_facility_id)

            # check if source facility contains the sku3 config
            self.assertTrue(SKU3_ID in source_facility.skus)

        env.step(None)

        # check state
        features = (
            "id",
            "facility_id",
            "product_id",
            "order_cost",
            "purchased",
            "received",
            "order_product_cost"
        )

        consumer_nodes = env.snapshot_list["consumer"]

        states = consumer_nodes[env.frame_index:sku3_consumer_data_model_index:features].flatten().astype(np.int)

        self.assertEqual(sku3_consumer_unit_id, states[0])
        self.assertEqual(SKU3_ID, states[2])

        env.reset()
        env.step(None)

        states = consumer_nodes[env.frame_index:sku3_consumer_data_model_index:features].flatten().astype(np.int)

        # Nothing happened at tick 0, so most states will be 0
        self.assertTrue((states[4:] == 0).all())

        self.assertEqual(sku3_consumer_unit_id, states[0])
        self.assertEqual(SKU3_ID, states[2])

    def test_consumer_action(self):
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        sku3_consumer_unit_id: Optional[int] = None
        sku3_consumer_unit: Optional[ConsumerUnit] = None
        sku3_supplier_facility_id: Optional[int] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for facility_info in facility_info_dict.values():
            if facility_info.name == "Supplier_SKU1":
                sku3_consumer_unit_id = facility_info.products_info[SKU3_ID].consumer_info.id
                sku3_consumer_unit = be.world.get_entity_by_id(sku3_consumer_unit_id)
            if facility_info.name == "Supplier_SKU3":
                sku3_supplier_facility_id = facility_info.id
        self.assertTrue(all([
            sku3_consumer_unit_id is not None,
            sku3_consumer_unit is not None,
            sku3_supplier_facility_id is not None
        ]))

        sku3_consumer_data_model_index = env.summary["node_mapping"]["unit_mapping"][sku3_consumer_unit_id][1]

        # zero quantity will be ignore
        action_with_zero = ConsumerAction(sku3_consumer_unit_id, SKU3_ID, sku3_supplier_facility_id, 0, "train")

        action = ConsumerAction(sku3_consumer_unit_id, SKU3_ID, sku3_supplier_facility_id, 10, "train")

        sku3_consumer_unit.set_action(action_with_zero)

        env.step(None)

        features = (
            "id",
            "facility_id",
            "product_id",
            "order_cost",
            "product_id",
            "purchased",
            "received",
            "order_product_cost"
        )

        consumer_nodes = env.snapshot_list["consumer"]

        states = consumer_nodes[env.frame_index:sku3_consumer_data_model_index:features].flatten().astype(np.int)

        # Nothing happened at tick 0, at the action will be recorded
        self.assertEqual(action_with_zero.product_id, states[4])
        self.assertEqual(action_with_zero.quantity, states[5])

        self.assertEqual(sku3_consumer_unit_id, states[0])
        self.assertEqual(SKU3_ID, states[2])

        # NOTE: we cannot set_action directly here, as post_step will clear the action before starting next tick
        env.step([action])

        self.assertEqual(action.quantity, sku3_consumer_unit._purchased)
        self.assertEqual(0, sku3_consumer_unit._received)

        states = consumer_nodes[env.frame_index:sku3_consumer_data_model_index:features].flatten().astype(np.int)

        # action field should be recorded
        self.assertEqual(action.product_id, states[4])
        self.assertEqual(action.quantity, states[5])

        # purchased same as quantity
        self.assertEqual(action.quantity, states[5])

        # no receives
        self.assertEqual(0, states[6])

        # same action for next step, so total_XXX will be changed to double
        env.step([action])

        states = consumer_nodes[env.frame_index:sku3_consumer_data_model_index:features].flatten().astype(np.int)

        # action field should be recorded
        self.assertEqual(action.product_id, states[4])
        self.assertEqual(action.quantity, states[5])

        # purchased same as quantity
        self.assertEqual(action.quantity, states[5])

        # no receives
        self.assertEqual(0, states[6])

    def test_consumer_on_order_reception(self):
        env = build_env("case_01", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        sku3_consumer_unit_id: Optional[int] = None
        sku3_consumer_unit: Optional[ConsumerUnit] = None
        sku3_supplier_facility_id: Optional[int] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for facility_info in facility_info_dict.values():
            if facility_info.name == "Supplier_SKU1":
                sku3_consumer_unit_id = facility_info.products_info[SKU3_ID].consumer_info.id
                sku3_consumer_unit = be.world.get_entity_by_id(sku3_consumer_unit_id)
            if facility_info.name == "Supplier_SKU3":
                sku3_supplier_facility_id = facility_info.id
        self.assertTrue(all([
            sku3_consumer_unit_id is not None,
            sku3_consumer_unit is not None,
            sku3_supplier_facility_id is not None
        ]))

        action = ConsumerAction(sku3_consumer_unit_id, SKU3_ID, sku3_supplier_facility_id, 10, "train")

        # 1st step must none action
        env.step(None)

        env.step([action])

        # simulate purchased product is arrived by vehicle unit
        sku3_consumer_unit.on_order_reception(sku3_supplier_facility_id, SKU3_ID, 10, 10)

        # now all order is done
        self.assertEqual(0, sku3_consumer_unit._open_orders[sku3_supplier_facility_id][SKU3_ID])
        self.assertEqual(10, sku3_consumer_unit._received)

        env.step(None)

        # NOTE: we cannot test the received state by calling on_order_reception directly,
        # as it will be cleared by env.step, do it on vehicle unit test.

    """
    Vehicle unit test:

    . initial state
    . if vehicle arrive at destination within special vlt
    . schedule job
    . try_load until patient <= 0 to cancel the schedule
    . try_load until patient > 0 to load order
    . try_unload
        . target storage cannot take all
        . target storage can take all
    """

    def test_vehicle_unit_state(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit we meet
        vehicle_unit: Optional[VehicleUnit] = None

        for unit_id, info in env.summary["node_mapping"]["unit_mapping"].items():
            if info[0] == "vehicle":
                vehicle_unit = be.world.get_entity_by_id(unit_id)

                break
        self.assertTrue(vehicle_unit is not None)

        # check initial state according to configuration file
        self.assertEqual(10, vehicle_unit._max_patient)

        self.assertEqual(0, vehicle_unit.requested_quantity)
        # not destination at first
        self.assertIsNone(vehicle_unit._destination)
        # no product
        self.assertEqual(0, vehicle_unit.product_id)
        # no steps
        self.assertEqual(0, vehicle_unit._remaining_steps)
        #
        self.assertEqual(0, vehicle_unit.payload)
        #
        self.assertEqual(0, vehicle_unit._steps)

        # state in frame
        self.assertEqual(0, vehicle_unit.data_model.payload)
        self.assertEqual(12, vehicle_unit.data_model.unit_transport_cost)

        # reset to check again
        env.step(None)
        env.reset()

        # check initial state according to configuration file
        self.assertEqual(10, vehicle_unit._max_patient)

        # not destination at first
        self.assertIsNone(vehicle_unit._destination)
        # no product
        self.assertEqual(0, vehicle_unit.product_id)
        # no steps
        self.assertEqual(0, vehicle_unit._remaining_steps)
        #
        self.assertEqual(0, vehicle_unit.payload)
        #
        self.assertEqual(0, vehicle_unit._steps)
        #
        self.assertEqual(0, vehicle_unit.requested_quantity)

        # state in frame
        self.assertEqual(0, vehicle_unit.data_model.payload)
        self.assertEqual(12, vehicle_unit.data_model.unit_transport_cost)

    def test_vehicle_unit_schedule(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit of Supplier
        vehicle_unit: Optional[VehicleUnit] = None
        dest_facility: Optional[FacilityBase] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Supplier_SKU3":
                for v in info.distribution_info.children:
                    vehicle_unit = be.world.get_entity_by_id(v.id)

            if info.name == "Warehouse_001":
                dest_facility = be.world.get_facility_by_id(info.id)
        self.assertTrue(all([vehicle_unit is not None, dest_facility is not None]))

        # make sure the upstream in the only one supplier in config
        self.assertEqual(1, len(dest_facility.upstream_vlt_infos))
        self.assertEqual(1, len(dest_facility.upstream_vlt_infos[SKU3_ID]))

        # schedule job vehicle unit manually, from supplier to warehouse
        vehicle_unit.schedule(dest_facility, SKU3_ID, 20, 2)

        # step to take snapshot
        env.step(None)

        vehicle_nodes = env.snapshot_list["vehicle"]

        # check internal states
        self.assertEqual(dest_facility, vehicle_unit._destination)
        self.assertEqual(SKU3_ID, vehicle_unit.product_id)
        self.assertEqual(20, vehicle_unit.requested_quantity)
        self.assertEqual(2, vehicle_unit._remaining_steps)

        features = (
            "id",
            "facility_id",
            "payload",
            "unit_transport_cost"
        )

        states = vehicle_nodes[env.frame_index:vehicle_unit.data_model_index:features].flatten().astype(np.int)

        # source id
        self.assertEqual(vehicle_unit.facility.id, states[1])
        # payload should be 20, as we already env.step
        self.assertEqual(20, states[2])

        # push the vehicle on the way
        env.step(None)

        states = vehicle_nodes[env.frame_index:vehicle_unit.data_model_index:features].flatten().astype(np.int)

        # payload
        self.assertEqual(20, states[2])

        env.step(None)
        env.step(None)

        # next step vehicle will try to unload the products
        env.step(None)

        states = vehicle_nodes[env.frame_index:vehicle_unit.data_model_index:features].flatten().astype(np.int)

        # the product is unloaded, vehicle states will be reset to initial
        # not destination at first
        self.assertIsNone(vehicle_unit._destination)
        self.assertEqual(0, vehicle_unit.product_id)
        self.assertEqual(0, vehicle_unit._remaining_steps)
        self.assertEqual(0, vehicle_unit.payload)
        self.assertEqual(0, vehicle_unit._steps)
        self.assertEqual(0, vehicle_unit.requested_quantity)

        # check states
        self.assertEqual(0, states[2])
        self.assertEqual(12, vehicle_unit.data_model.unit_transport_cost)

    def test_vehicle_unit_no_patient(self):
        """
        NOTE: with patient is tried in above case after schedule the job
        """
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit of Supplier
        vehicle_unit: Optional[VehicleUnit] = None
        dest_facility: Optional[FacilityBase] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Supplier_SKU3":
                for v in info.distribution_info.children:
                    vehicle_unit = be.world.get_entity_by_id(v.id)

            if info.name == "Warehouse_001":
                dest_facility = be.world.get_facility_by_id(info.id)
        self.assertTrue(all([vehicle_unit is not None, dest_facility is not None]))

        # there is 80 sku3 in supplier, lets schedule a job for 100, to make sure it will fail to try load
        vehicle_unit.schedule(dest_facility, SKU3_ID, 100, 3)

        # push env to next step
        env.step(None)

        self.assertEqual(100, vehicle_unit.requested_quantity)

        # the patient will -1 as no enough product so load
        self.assertEqual(10 - 1, vehicle_unit._remaining_patient)

        # no payload
        self.assertEqual(0, vehicle_unit.payload)
        self.assertEqual(0, vehicle_unit.data_model.payload)

        # step 9 ticks, patient will be 0
        for i in range(10 - 1):
            env.step(None)

            self.assertEqual(10 - 1 - (i + 1), vehicle_unit._remaining_patient)

        vehicle_nodes = env.snapshot_list["vehicle"]
        features = (
            "id",
            "facility_id",
            "payload",
            "unit_transport_cost"
        )

        states = vehicle_nodes[:vehicle_unit.data_model_index:"payload"].flatten().astype(np.int)

        # no payload from start to now
        self.assertListEqual([0] * 10, list(states))

        # push env to next step, vehicle will be reset to initial state
        env.step(None)

        states = vehicle_nodes[env.frame_index:vehicle_unit.data_model_index:features].flatten().astype(np.int)

        # the product is unloaded, vehicle states will be reset to initial
        # not destination at first
        self.assertIsNone(vehicle_unit._destination)
        self.assertEqual(0, vehicle_unit.product_id)
        self.assertEqual(0, vehicle_unit._remaining_steps)
        self.assertEqual(0, vehicle_unit.payload)
        self.assertEqual(0, vehicle_unit._steps)
        self.assertEqual(0, vehicle_unit.requested_quantity)

        # check states

        self.assertEqual(0, states[2])
        self.assertEqual(12, vehicle_unit.data_model.unit_transport_cost)

    def test_vehicle_unit_cannot_unload_at_destination(self):
        """
        NOTE: If vehicle cannot unload at destination, it will keep waiting, until success to unload.

        """
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit of Supplier
        vehicle_unit: Optional[VehicleUnit] = None
        dest_facility: Optional[FacilityBase] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Supplier_SKU3":
                for v in info.distribution_info.children:
                    vehicle_unit = be.world.get_entity_by_id(v.id)

            if info.name == "Warehouse_001":
                dest_facility = be.world.get_facility_by_id(info.id)
        self.assertTrue(all([vehicle_unit is not None, dest_facility is not None]))

        # move all 80 sku3 to destination, will cause vehicle keep waiting there
        vehicle_unit.schedule(dest_facility, SKU3_ID, 80, 2)

        # step to the end.
        is_done = False

        while not is_done:
            _, _, is_done = env.step(None)

        vehicle_nodes = env.snapshot_list["vehicle"]

        # payload should be 80 for first 4 ticks, as it is on the way
        # then it will unload 100 - 10 - 10 - 10 = 70 products, as this is the remaining space of destination storage
        # so then it will keep waiting to unload remaining 10
        payload_states = vehicle_nodes[:vehicle_unit.data_model_index:"payload"].flatten().astype(np.int)
        self.assertListEqual([80] * 3 + [10] * 97, list(payload_states))

    """
    Distribution unit test:

    . initial state
    . place order
    . dispatch orders without available vehicle
    . dispatch order with vehicle
    """

    def test_distribution_unit_initial_state(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit of Supplier
        dist_unit: Optional[DistributionUnit] = None
        dest_facility: Optional[FacilityBase] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Supplier_SKU3":
                dist_unit = be.world.get_entity_by_id(info.distribution_info.id)

            if info.name == "Warehouse_001":
                dest_facility = be.world.get_facility_by_id(info.id)
        self.assertTrue(all([dist_unit is not None, dest_facility is not None]))

        self.assertEqual(0, sum([len(order_queue) for order_queue in dist_unit._order_queues.values()]))

        # reset
        env.reset()

        self.assertEqual(0, sum([len(order_queue) for order_queue in dist_unit._order_queues.values()]))

    def test_distribution_unit_dispatch_order(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # try to find first vehicle unit of Supplier
        dist_unit: Optional[DistributionUnit] = None
        dest_facility: Optional[FacilityBase] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Supplier_SKU3":
                dist_unit = be.world.get_entity_by_id(info.distribution_info.id)

            if info.name == "Warehouse_001":
                dest_facility = be.world.get_facility_by_id(info.id)
        self.assertTrue(all([dist_unit is not None, dest_facility is not None]))

        first_vehicle: VehicleUnit = dist_unit.vehicles["train"][0]

        order = Order(dest_facility, SKU3_ID, 10, "train", 2)

        dist_unit.place_order(order)

        # check if order is saved
        self.assertEqual(1, sum([len(order_queue) for order_queue in dist_unit._order_queues.values()]))

        # check get pending order correct
        pending_order = dist_unit.get_pending_product_quantities()

        self.assertDictEqual({3: 10}, pending_order)

        # same as vehicle schedule case, distribution will try to schedule this order to vehicles from beginning to end
        # so it will dispatch this order to first vehicle
        env.step(None)

        self.assertEqual(dest_facility, first_vehicle._destination)
        self.assertEqual(10, first_vehicle.requested_quantity)
        self.assertEqual(SKU3_ID, first_vehicle.product_id)

        # since we already test vehicle unit, do not check the it again here

        # add another order to check pending order
        dist_unit.place_order(order)

        pending_order = dist_unit.get_pending_product_quantities()

        self.assertDictEqual({3: 10}, pending_order)

        # another order, will cause the pending order increase
        dist_unit.place_order(order)

        pending_order = dist_unit.get_pending_product_quantities()

        # 2 pending orders
        self.assertDictEqual({3: 20}, pending_order)

        # now we have only one available vehicle, 2 pending order
        # next step will cause delay_order_penalty
        env.step(None)

        second_vehicle = dist_unit.vehicles["train"][1]

        self.assertEqual(dest_facility, second_vehicle._destination)
        self.assertEqual(10, second_vehicle.requested_quantity)
        self.assertEqual(SKU3_ID, second_vehicle.product_id)

    """
    Seller unit test:
        . initial state
        . with a customized seller unit
        . with built in one
    """

    def test_seller_unit_initial_states(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # find seller for sku3 from retailer facility
        sell_unit: Optional[SellerUnit] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Retailer_001":
                for pinfo in info.products_info.values():
                    if pinfo.product_id == SKU3_ID:
                        sell_unit = be.world.get_entity_by_id(pinfo.seller_info.id)
        self.assertTrue(sell_unit is not None)

        # from configuration
        self.assertEqual(10, sell_unit._gamma)

        self.assertEqual(0, sell_unit._sold)
        self.assertEqual(0, sell_unit._demand)
        self.assertEqual(0, sell_unit._total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

        #
        self.assertEqual(0, sell_unit.data_model.sold)
        self.assertEqual(0, sell_unit.data_model.demand)
        self.assertEqual(0, sell_unit.data_model.total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

        env.reset()

        # from configuration
        self.assertEqual(10, sell_unit._gamma)
        self.assertEqual(0, sell_unit._sold)
        self.assertEqual(0, sell_unit._demand)
        self.assertEqual(0, sell_unit._total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

        #
        self.assertEqual(0, sell_unit.data_model.sold)
        self.assertEqual(0, sell_unit.data_model.demand)
        self.assertEqual(0, sell_unit.data_model.total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

    def test_seller_unit_demand_states(self):
        env = build_env("case_02", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # find seller for sku3 from retailer facility
        sell_unit: Optional[SellerUnit] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Retailer_001":
                for pinfo in info.products_info.values():
                    if pinfo.product_id == SKU3_ID:
                        sell_unit = be.world.get_entity_by_id(pinfo.seller_info.id)
        self.assertTrue(sell_unit is not None)

        sku3_init_quantity = sell_unit.facility.skus[SKU3_ID].init_stock

        env.step(None)

        # seller unit will try to count down the product quantity based on demand
        # default seller use gamma distribution on each tick
        demand = sell_unit._demand

        # demand should be same with original
        self.assertEqual(demand, sell_unit.data_model.demand)

        actual_sold = min(demand, sku3_init_quantity)
        # sold may be not same as demand, depend on remaining quantity in storage
        self.assertEqual(actual_sold, sell_unit._sold)
        self.assertEqual(actual_sold, sell_unit.data_model.sold)
        self.assertEqual(actual_sold, sell_unit._total_sold)
        self.assertEqual(actual_sold, sell_unit.data_model.total_sold)

        states = env.snapshot_list["seller"][
                 env.frame_index:sell_unit.data_model_index:("sold", "demand", "total_sold")].flatten().astype(np.int)

        self.assertEqual(actual_sold, states[0])
        self.assertEqual(demand, states[1])
        self.assertEqual(actual_sold, states[2])

        # move to next step to check if state is correct
        env.step(None)

        demand = sell_unit._demand

        # demand should be same with original
        self.assertEqual(demand, sell_unit.data_model.demand)

        actual_sold_2 = min(demand, sku3_init_quantity - actual_sold)

        # sold may be not same as demand, depend on remaining quantity in storage
        self.assertEqual(actual_sold_2, sell_unit._sold)
        self.assertEqual(actual_sold_2, sell_unit.data_model.sold)
        self.assertEqual(actual_sold + actual_sold_2, sell_unit._total_sold)
        self.assertEqual(actual_sold + actual_sold_2, sell_unit.data_model.total_sold)

        states = env.snapshot_list["seller"][
                 env.frame_index:sell_unit.data_model_index:("sold", "demand", "total_sold")].flatten().astype(np.int)

        self.assertEqual(actual_sold_2, states[0])
        self.assertEqual(demand, states[1])
        self.assertEqual(actual_sold + actual_sold_2, states[2])

    def test_seller_unit_customized(self):
        env = build_env("case_03", 100)
        be = env.business_engine
        assert isinstance(be, SupplyChainBusinessEngine)

        # find seller for sku3 from retailer facility
        sell_unit: Optional[SellerUnit] = None

        facility_info_dict: Dict[int, FacilityInfo] = env.summary["node_mapping"]["facilities"]
        for info in facility_info_dict.values():
            if info.name == "Retailer_001":
                for pinfo in info.products_info.values():
                    if pinfo.product_id == SKU3_ID:
                        sell_unit = be.world.get_entity_by_id(pinfo.seller_info.id)
        self.assertTrue(sell_unit is not None)

        # NOTE:
        # this simple seller unit return demands that same as current tick
        env.step(None)

        # so tick 0 will have demand == 0
        # from configuration
        self.assertEqual(0, sell_unit._sold)
        self.assertEqual(0, sell_unit._demand)
        self.assertEqual(0, sell_unit._total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

        #
        self.assertEqual(0, sell_unit.data_model.sold)
        self.assertEqual(0, sell_unit.data_model.demand)
        self.assertEqual(0, sell_unit.data_model.total_sold)
        self.assertEqual(SKU3_ID, sell_unit.product_id)

        is_done = False

        while not is_done:
            _, _, is_done = env.step(None)

        # check demand history, it should be same as tick
        seller_nodes = env.snapshot_list["seller"]

        demand_states = seller_nodes[:sell_unit.data_model_index:"demand"].flatten().astype(np.int)

        self.assertListEqual([i for i in range(100)], list(demand_states))

        # check sold states
        # it should be 0 after tick 4
        sold_states = seller_nodes[:sell_unit.data_model_index:"sold"].flatten().astype(np.int)
        self.assertListEqual([0, 1, 2, 3, 4] + [0] * 95, list(sold_states))

        # total sold
        total_sold_states = seller_nodes[:sell_unit.data_model_index:"total_sold"].flatten().astype(np.int)
        # total sold will keep same after tick 4
        self.assertListEqual([0, 1, 3, 6, 10] + [10] * 95, list(total_sold_states))


if __name__ == '__main__':
    unittest.main()
