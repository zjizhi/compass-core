# Copyright 2014 Huawei Technologies Co. Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Module to provider function to poll switch."""
import logging

from compass.db import database
from compass.db.model import Machine
from compass.db.model import Switch
from compass.db.model import SwitchConfig
from compass.hdsdiscovery.hdmanager import HDManager


def poll_switch(ip_addr, req_obj='mac', oper="SCAN"):
    """Query switch and return expected result

    .. note::
       When polling switch succeeds, for each mac it got from polling switch,
       A Machine record associated with the switch is added to the database.

    :param ip_addr: switch ip address.
    :type ip_addr: str
    :param req_obj: the object requested to query from switch.
    :type req_obj: str
    :param oper: the operation to query the switch.
    :type oper: str, should be one of ['SCAN', 'GET', 'SET']

    .. note::
       The function should be called out of database session scope.

    """
    under_monitoring = 'under_monitoring'
    unreachable = 'unreachable'

    if not ip_addr:
        logging.error('No switch IP address is provided!')
        return

    with database.session() as session:
        #Retrieve vendor info from switch table
        switch = session.query(Switch).filter_by(ip=ip_addr).first()
        logging.info("pollswitch: %s", switch)
        if not switch:
            logging.error('no switch found for %s', ip_addr)
            return

        credential = switch.credential
        logging.info("pollswitch: credential %r", credential)
        vendor = switch.vendor
        prev_state = switch.state
        hdmanager = HDManager()

        vendor, vstate, err_msg = hdmanager.get_vendor(ip_addr, credential)
        if not vendor:
            switch.state = vstate
            switch.err_msg = err_msg
            logging.info("*****error_msg: %s****", switch.err_msg)
            logging.error('no vendor found or match switch %s', switch)
            return

        switch.vendor = vendor

        # Start to poll switch's mac address.....
        logging.debug('hdmanager learn switch from %s %s %s %s %s',
                      ip_addr, credential, vendor, req_obj, oper)
        results = []

        try:
            results = hdmanager.learn(
                ip_addr, credential, vendor, req_obj, oper)
        except Exception as error:
            logging.exception(error)
            switch.state = unreachable
            switch.err_msg = "SNMP walk for querying MAC addresses timedout"
            return

        logging.info("pollswitch %s result: %s", switch, results)
        if not results:
            logging.error('no result learned from %s %s %s %s %s',
                          ip_addr, credential, vendor, req_obj, oper)
            return

        switch_id = switch.id
        filter_ports = session.query(
            SwitchConfig.filter_port
        ).filter(
            SwitchConfig.ip == Switch.ip
        ).filter(
            Switch.id == switch_id
        ).all()
        logging.info("***********filter posts are %s********", filter_ports)
        if filter_ports:
            #Get all ports from tuples into list
            filter_ports = [i[0] for i in filter_ports]

        for entry in results:
            mac = entry['mac']
            port = entry['port']
            vlan = entry['vlan']
            if port in filter_ports:
                continue

            machine = session.query(Machine).filter_by(
                mac=mac, switch_id=switch_id).first()
            if not machine:
                machine = Machine(
                    mac=mac, port=port, vlan=vlan, switch_id=switch_id)
                session.add(machine)
            else:
                machine.port = port
                machine.vlan = vlan

        logging.debug('update switch %s state to under monitoring', switch)
        if prev_state != under_monitoring:
            #Update error message in db
            switch.err_msg = ""

        switch.state = under_monitoring
