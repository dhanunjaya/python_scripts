import argparse
import os
import re
import sys
import json
import netaddr
import time
import pprint
import logging

from keystoneclient.v2_0 import client as keystoneclient
try:
    from neutronclient.neutron import client as neutronclient
except:
    from quantumclient.quantum import client as neutronclient
from novaclient import client as novaclient


TRANSIT_GATEWAY_POSITION=6

LOG = logging.getLogger('create_pass_net')
LOG_FORMAT='%(asctime)s %(name)-12s %(levelname)-8s %(message)s'
LOG_DATE = '%m-%d %H:%M'
DESCRIPTION = "DMZ Network"

def setup_logging(args):
    level = logging.INFO
    if args.debug:
        level = logging.DEBUG
    logging.basicConfig(level=level, format=LOG_FORMAT, date_fmt=LOG_DATE)
    handler = SysLogHandler(address='/dev/log')
    syslog_formatter = logging.Formatter('%(name)s: %(levelname)s %(message)s')
    handler.setFormatter(syslog_formatter)
    LOG.addHandler(handler)


def parse_args():
    for key in ['OS_USERNAME', 'OS_PASSWORD', 'OS_TENANT_NAME', 'OS_AUTH_URL']:
        if key not in os.environ.keys():
            print("Environment is missing")

    ap = argparse.ArgumentParser()
    ap.add_argument('-d', '--debug', action='store_true', default=False, help='Show debugging output')
    ap.add_argument('-c', '--config', action='store', default="local.conf", help="Path to configuration file")
    ap.add_argument('-U', '--OS_USERNAME', action="store", dest="admin_username",
                    help='username to authenticate with to perform the administrator level actions required by this script.')
    ap.add_argument('-P', '--OS_PASSWORD', action="store", dest="admin_password",
                     help='password for the user specified by OS_USERNAME.')
    ap.add_argument('-T', '--OS_TENANT_NAME', action="store", dest="admin_project", help='project name where the authenticating\
                    user has the admin role. This script will NOT work if this use does not have the admin role for this project.')
    ap.add_argument('-A', '--OS_AUTH_URL', action="store", dest="auth_url", help='keystone auth url')

    return ap.parse_args()

def validate(nclient, kclient, line_num, tenant_id, tenant_name, overlay_subnet, transit_vlan, transit_vlan_label, transit_subnet):
    # ensure tenant id is in the acceptable format and exists here
    try:
        tenant_id = str(tenant_id)[0:36]
        current_tenant_list = kclient.tenants.list()
        if tenant_id not in [k.id for k in current_tenant_list]:
            print("tenant_id %s on line number %s not found in this environment" % (tenant_id, line_num))
        return False
    except:
        print("tenant_id %s on line number %s is not a valid tenant_id" % (tenant_id, line_num))
        return False

    # ensure vlan id is an integer
    try:
        transit_vlan = int(transit_vlan)
    except:
        print("vlan id %s on line number %s is not a valid integer" % (transit_vlan, line_num))
        return False

    # ensure we can retrieve the transit_gateway from the transit_subnet
    try:
        generate_transit_gateway(transit_subnet)
    except:
        print("Unable to retrieve sixth usable address from transit subnet %s on line number %s" % (transit_subnet, line_num))
        return False

    # ensure the overlay subnet is valid
    try:
        overlay_subnet = netaddr.IPNetwork(overlay_subnet)
    except:
        print("Unable to parse the overlay subnet %s on line number %s" % (overlay_subnet, line_num))
        return False

    # ensure the dmz subnet is valid
    try:
        dmz_subnet = netaddr.IPNetwork(dmz_subnet)
    except:
        print("Unable to parse the dmz subnet %s on line number %s" % (dmz_subnet, line_num))
        return False

    # passed known validation
    return True

def generate_name_router(vlan):
    return 'CONEXUS_ROUTER_' + str(vlan)

def generate_name_network_overlay(vlan):
    return 'CONEXUS_OVERLAY_' + str(vlan)

def generate_name_network_transit(vlan):
    return 'CONEXUS_TRANSIT_' + str(vlan)

def generate_name_subnet_overlay(vlan):
    return 'CONEXUS_OVERLAY_' + str(vlan)

def generate_name_subnet_transit(vlan):
    return 'CONEXUS_TRANSIT_' + str(vlan)

def generate_name_subnet_dmz(vlan):
    return 'DMZ_NETWORK_' + str(vlan)

def generate_name_network_dmz(vlan):
    return 'DMZ_NETWORK_' + str(vlan)

def generate_name_router_dmz(vlan):
    return 'DMZ_ROUTER_' + str(vlan)

def generate_transit_host(transit_subnet):
    transit_subnet = netaddr.IPNetwork(transit_subnet)
    transit_gateway = str(transit_subnet[TRANSIT_GATEWAY_POSITION])
    return transit_gateway

def router_name_to_id(nclient, name, tenant_id):
	for router in nclient.list_routers()['routers']:
		if router['name'] == name and router['tenant_id'] == tenant_id:
			return router['id']

def network_name_to_id(nclient, name, tenant_id):
	for network in nclient.list_networks()['networks']:
		if network['name'] == name and network['tenant_id'] == tenant_id:
			return network['id']

def subnet_name_to_id(nclient, name, tenant_id):
	for subnet in nclient.list_subnets()['subnets']:
		if subnet['name'] == name and subnet['tenant_id'] == tenant_id:
			return subnet['id']

def create_network(nclient, tenant_id, tenant_name, overlay_subnet, transit_vlan, 
                    transit_vlan_label, transit_subnet, dmz_subnet):

    router_name = generate_name_router(transit_vlan)
    overlay_network_name = generate_name_network_overlay(transit_vlan)
    transit_network_name = generate_name_network_transit(transit_vlan)
    overlay_subnet_name = generate_name_subnet_overlay(transit_vlan)
    transit_subnet_name = generate_name_subnet_transit(transit_vlan)
    dmz_network_name = generate_name_network_dmz(transit_vlan)
    dmz_subnet_name = generate_name_subnet_dmz(transit_vlan)
    dmz_router_name = generate_name_router_dmz(transit_vlan)

    # DMZ Router
    dmz_router_id = router_name_to_id(nclient, dmz_router_name, tenant_id)
    if not dmz_router_id:
        Log.info("Creating non-existent dmz router %s" % dmz_router_name)
        dmz_router_id = nclient.create_router(body={"router" : {"name": dmz_router_name, "tenant_id": tenant_id}})['router']['id']

    # DMZ Network
    dmz_network_id = network_name_to_id(nclient, dmz_network_name, tenant_id)
    if not dmz_network_id:
    	Log.info("Creating non-existent dmz network %s" % dmz_network_name)
    	dmz_network_id = nclient.create_network(body={"network" : {"name": dmz_network_name,
                                                    "admin_state_up": "true", "tenant_id": tenant_id,
                                                    "router:external":"true"}})['network']['id']

    # DMZ Subnet
    dmz_subnet_id = subnet_name_to_id(nclient, dmz_subnet_name, tenant_id)
    if not dmz_subnet_id:
    	LOG.info("Creating non-existent dmz subnet %s" % dmz_subnet_name)
    	dmz_subnet_id = nclient.create_subnet(body={"subnet" : {"name": dmz_subnet_name, "network_id": dmz_network_id,
                                                "tenant_id": tenant_id, "ip_version": 4,
                                                "cidr": dmz_subnet}})['subnet']['id']

    nclient.add_gateway_router(router=dmz_router_id, body={"network_id": dmz_network_id, "enable_snat": False})


    # Router to join Overlay Network and Transit Network
    router_id = router_name_to_id(nclient, router_name, tenant_id)
    if not router_id:
        LOG.info("Creating non-existent router %s" % router_name)
        router_id = nclient.create_router(body={"router" : {"name": router_name,"tenant_id": tenant_id}})['router']['id']

    # Overlay Network
    overlay_network_id = network_name_to_id(nclient, overlay_network_name, tenant_id)
    if not overlay_network_id:
    	LOG.info("Creating non-existent overlay network %s" % overlay_network_name)
    	overlay_network_id = nclient.create_network(body={"network" : {"name": overlay_network_name,
                                                    "admin_state_up": "true", "tenant_id": tenant_id}})['network']['id']

    #Overlay Subnet	
    overlay_subnet_id = subnet_name_to_id(nclient, overlay_subnet_name, tenant_id)
    if not overlay_subnet_id:
        LOG.info("Creating non-existent overlay subnet %s" % overlay_subnet_name)
        overlay_subnet_id = nclient.create_subnet(body={"subnet" : {"name":overlay_subnet_name,
                                                    "network_id":overlay_network_id,
                                                    "tenant_id": tenant_id,"ip_version":4,
                                                    "cidr":overlay_subnet}})['subnet']['id']

    # Transit Network
    transit_network_id = network_name_to_id(nclient, transit_network_name, tenant_id)
    if not transit_network_id:
        LOG.info("Creating non-existent transit network %s" % transit_network_name)
        transit_network_id = nclient.create_network(body={"network" : {"name": transit_network_name, "admin_state_up": "true",
                                                    "router:external": "true", "tenant_id": tenant_id, "router:private": "true",
                                                    "provider:network_type": "vlan", "provider:physical_network": transit_vlan_label,
                                                    "provider:segmentation_id": transit_vlan}})['network']['id']
    
    # Transit Subnet
    subnet_allocation_start = generate_transit_host(transit_subnet)
    subnet_allocation_end = generate_transit_host(transit_subnet)

    transit_subnet_id = subnet_name_to_id(nclient, transit_subnet_name, tenant_id)
    if not transit_subnet_id:
        LOG.info("Creating non-existent transit subnet %s" % transit_subnet_name)
        transit_subnet_id = nclient.create_subnet(body={"subnet" : {"name":transit_subnet_name,"network_id":transit_network_id,
                                                    "tenant_id": tenant_id,"ip_version":4,"cidr":transit_subnet,'enable_dhcp':False,
                                                    "allocation_pools":[{"start":subnet_allocation_start,
                                                    "end":subnet_allocation_end}]}})['subnet']['id']

    nclient.add_gateway_router(router=router_id, body={"network_id": transit_network_id})
    try:
        nclient.add_interface_router(router=router_id, body={"subnet_id": overlay_subnet_id})
        LOG.info("Creating non-existent attachment of overlay network to router id %s" % router_id)
    except:
        pass


    return (dmz_network_id, overlay_network_id)


def create_vms(nova_client, image_id, flavor_id, vms_count, dmz_network_id, overlay_network_id):
    server = None
    for i in xrange(int(vms_count)):
        server = nova_client.servers.create(name = "PaaS-VM-" + str(i), image = image_id, 
                                            flavor = flavor_id, 
                                            nics = [{"net-id": dmz_network_id}, {"net-id": overlay_network_id}])
        if server != None:
            time.sleep(20)
            if server.status == 'ERROR':
                LOG.info("Instance is in error state")


def read_config(nclient, kclient, nova_client, config_path):
    with open(config_path, 'r') as f:
        line_num = 0
        for config_entry in f.readlines():
            line_num += 1
            if re.match('#.*', config_entry):
                continue
            (tenant_id, tenant_name, image_id, flavor_id, vms_count, overlay_subnet, 
                transit_vlan, transit_vlan_label, transit_subnet, dmz_subnet) = config_entry.split(",")
            if DEBUG == 1:
                print(tenant_id, tenant_name, image_id, flavor_id, vms_count, overlay_subnet, transit_vlan, 
                transit_vlan_label, transit_subnet, dmz_subnet)
            if not validate(nclient, kclient, line_num, tenant_id, tenant_name, overlay_subnet, transit_vlan, transit_vlan_label, transit_subnet):
                continue
            (dmz_network_id, overlay_network_id) = create_network(nclient, tenant_id, tenant_name, overlay_subnet, 
                                                                    transit_vlan, transit_vlan_label, transit_subnet, dmz_subnet)
            create_vms(nova_client, image_id, flavor_id, vms_count, dmz_network_id, overlay_network_id)

def run(args):
    admin_username = args.admin_username
    if admin_username == None:
        admin_username = os.environ.get('OS_USERNAME')
        if admin_username == None:
            print 'Error: You need to supply the --OS_USERNAME argument to this script, or define this environment variable'
            sys.exit(1)

    admin_password = args.admin_password
    if admin_password == None:
        admin_password = os.environ.get('OS_PASSWORD')
        if admin_password == None:
            print 'Error: You need to supply the --OS_PASSWORD argument to this script, or define this environment variable'
            sys.exit(1)

    admin_project = args.admin_project
    if admin_project == None:
        admin_project = os.environ.get('OS_TENANT_NAME')
        if admin_project == None:
            print 'Error: You need to supply the --OS_TENANT_NAME argument to this script, or define this environment variable'
            sys.exit(1)

    auth_url = args.auth_url
    if auth_url == None:
        auth_url = os.environ.get('OS_AUTH_URL')
        if auth_url == None:
            print 'Error: you need to supply the --OS_AUTH_URL argument to this script, or define this environment variable'
            sys.exit(1)

    # instantiate client
    kclient = keystoneclient.Client(auth_url=auth_url,
                                        username=admin_username,
                                        tenant_name=admin_project,
                                        password=admin_password)

    nclient = neutronclient.Client('2.0', auth_url=auth_url,
                                            username=admin_username,
                                            tenant_name=admin_project,
                                            password=admin_password)

    nova_client = novaclient.Client('2', auth_url=auth_url,
                                    username=admin_username,
                                    project_id=admin_project,
                                    api_key=admin_password)

    nclient.format = 'json'
    kclient.format = 'json'
    nova_client.format = 'json'
    
    LOG.info("Authenticated Successfully, performing sync")
        
    read_config(nclient, kclient, nova_client, args.config)

    if segments and segments_post:
        for seg in segments:
            for seg_upd in segments_post:
                if seg['segmentation_id'] != seg_upd['segmentation_id']:
                    segments_put.append(seg)
                if seg['segmentation_id'] == seg_upd['segmentation_id'] or (seg in segments_post):
                    if seg in segments_put:
                        segments_put.remove(seg)


if __name__ == '__main__':
    args = parse_args()
    setup_logging(args)
    run(args)
