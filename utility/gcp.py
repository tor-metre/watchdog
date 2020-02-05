""" This library file holds functions for interacting with the Google Compute Platform.
"""

from googleapiclient import discovery
from utils import zone_from_name, dict_to_location, location_to_dict
import logging


class GCP:
    # WARNING - This class is NOT Thread Safe

    def __init__(self, project, source_disk, instance_type, state_file_storage):
        self.compute = discovery.build('compute', 'v1')
        self.project = project
        self.source_disk = source_disk
        self.instance_type = instance_type
        self.state_file_storage = state_file_storage
        self.global_zones = self._fetch_zones()
        self.logger = logging.getLogger("utility." + __name__)
        self.logger.debug("Initialised logging for GCP Object attached to project {p}".format(p=project))

    def _fetch_zones(self):
        request = self.compute.zones().list(project=self.project)
        zone_names = set()
        while request is not None:
            response = request.execute()
            zone_names.update([z['id'] for z in response['items'] if z['deprecated']['state'] == "ACTIVE"])
            request = self.compute.zones().list_next(previous_request=request, previous_response=response)
        self.logger.debug("Fetched {zoneLen} zones from GCP API".format(zoneLen=len(zone_names)))
        return frozenset(zone_names)

    def _set_zones_if_empty(self, zones):
        if zones is None:
            return self.get_zones()
        else:
            assert (zones.issubset(self.get_zones()))
            return zones

    def get_zones(self):
        return self.global_zones

    def _create_instance(self, zone, name, location, state_file):
        assert (zone in self.global_zones)
        self.logger.debug("Creating an instance in {zone} with {name} of type {type}".format(zone=zone, name=name,
                                                                                             type=self.instance_type))
        machine_type_url = "zones/{zone}/machineTypes/{type}".format(zone=zone, type=self.instance_type)
        image_url = 'projects/{project}/global/images/{image}'.format(project=self.project, image=self.source_disk)
        permission_url = 'https://www.googleapis.com/auth/'
        config = {
            'name': name,
            'machineType': machine_type_url,
            'scheduling': {'preemptible': True},
            'disks': [{'boot': True, 'autoDelete': True, 'initializeParams': {'sourceImage': image_url}}],
            'networkInterfaces': [{'network': 'global/networks/default',
                                   'accessConfigs': [{'type': 'ONE_TO_ONE_NAT', 'name': 'External NAT'}]
                                   }],
            'serviceAccounts': [{'email': 'default',
                                 'scopes': [permission_url + 'devstorage.read_write', permission_url + 'logging.write']
                                 }],
            'metadata': {
                'items': [{
                    'key': 'shutdown-script',
                    'value': "./shutdown.sh"
                }, {
                    'key': 'location',
                    'value': location
                }, {
                    'key': 'stateFile',
                    'value': state_file
                }]
            }
        }
        return self.compute.instances().insert(
            project=self.project,
            zone=zone,
            body=config).execute()

    def new_instance(self, zone, browser, i):
        name = dict_to_location({
            'zone': zone,
            'browser': browser,
            'agent_id': i
        })
        state_file = "gs://hungry-serpent//{id}.state".format(id=i)
        return self._create_instance(zone, name, name, state_file)

    def start_instance(self, name):
        self.logger.debug("Starting instance {name}".format(name=name))
        zone = zone_from_name(name)
        return self.compute.instances().start(project=self.project, zone=zone, instance=name).execute()

    def stop_instance(self, name):
        self.logger.debug("Stopping instance {name}".format(name=name))
        zone = zone_from_name(name)
        return self.compute.instances().stop(project=self.project, zone=zone, instance=name).execute()

    def delete_instance(self, name):
        self.logger.debug("Deleting instance {name}".format(name=name))
        zone = zone_from_name(name)
        return self.compute.instances().delete(project=self.project, zone=zone, instance=name).execute()

    def get_instances(self, zones=None):
        zones = self._set_zones_if_empty(zones)
        results = list()
        for zone in zones:
            request = self.compute.instances().list(project=self.project, zone=zone)
            while request is not None:
                response = request.execute()
                for instance in response['items']:
                    idict = dict()
                    idict['name'] = instance['name']
                    idict['zone'] = zone
                    idict['creation_time'] = instance['creationTimestamp']
                    idict['status'] = instance['status']
                    if 'location' in instance['metadata'].keys():
                        idict['location'] = instance['metadata']['location']
                        idict['stateFile'] = instance['metadata']['stateFile']
                    results.append(idict)
                request = self.compute.instances().list_next(previous_request=request, previous_response=response)
        self.logger.debug("Discovered {instanceLen} in {zoneLen} zones".format(
            instanceLen=len(results), zoneLen=len(zones)))
        return results

    def get_running_instances(self, zones=None, instances=None):
        if instances is None:
            instances = self.get_instances(zones)
        return [r for r in instances if r['status'] == 'RUNNING']

    def get_stopped_instances(self, zones=None, instances=None):
        if instances is None:
            instances = self.get_instances(zones)
        return [r for r in instances if r['status'] == 'TERMINATED']

    def activate_instances(self, names, instances=None):
        initial_size = len(names)
        self.logger.debug(f"Attempting to activate {initial_size} instances.")
        names = set(names)
        if instances is None:
            instances = self.get_instances()
        to_restart = set()
        for i in instances:
            if i['name'] in names:
                if i['status'] == 'TERMINATED':
                    to_restart.add(i['name'])
                names.remove(i['name'])
        self.logger.debug(f"Restarting {len(to_restart)} instances")
        for t in to_restart:
            self.start_instance(t)
        self.logger.debug(f"Creating {len(names)} instances")
        for n in names:
            r = location_to_dict(n)
            self.new_instance(r['zone'], r['browser'], r['agent_id'])
        self.logger.debug(f"{initial_size} instances were in transitional state(s) and ignored.")

    def deactivate_instances(self,names,instances=None):
        if instances is None:
            instances = self.get_instances()
        stopped = 0
        for i in instances:
            if i['name'] in names and i['status'] == "RUNNING":
                self.stop_instance(i['name'])
                stopped += 1
        self.logger.debug(f"Deactivated {stopped} instance(s) out of {len(names)} requested")

