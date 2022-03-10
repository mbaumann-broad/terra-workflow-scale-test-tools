import csv
import functools
import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import currentThread, Thread
from typing import Tuple, Optional, Any

import google.auth
import requests
import schedule

from requests.exceptions import HTTPError

logging.basicConfig(filename="monitor_response_times_v2.log",
                    filemode="w",
                    level=logging.DEBUG)
logger = logging.getLogger()
run_date_time_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

class DeploymentInfo:
    @dataclass
    class TerraDeploymentInfo:
        bond_host: str
        bond_provider: str
        martha_host: str

    __terra_bdc_dev = TerraDeploymentInfo("broad-bond-dev.appspot.com",
                                          "fence",
                                          "us-central1-broad-dsde-dev.cloudfunctions.net")

    @dataclass
    class Gen3DeploymentInfo:
        gen3_host: str
        public_drs_uri: str
        cloud_uri_scheme: str = "gs"

    __gen3_bdc_staging = Gen3DeploymentInfo("staging.gen3.biodatacatalyst.nhlbi.nih.gov",
                                            "drs://dg.712C:dg.712C/fa640b0e-9779-452f-99a6-16d833d15bd0")

    def terra_factory(self) -> TerraDeploymentInfo:
        return self.__terra_bdc_dev

    def gen3_factory(self) -> Gen3DeploymentInfo:
        return self.__gen3_bdc_staging


class MonitoringUtilityMethods:

    def format_timestamp_as_utc(self, seconds_since_epoch: float):
        return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y/%m/%d %H:%M:%S")

    def monitoring_info(self, start_time: float, response: requests.Response):
        response_duration = round(time.time() - start_time, 3)
        response_code = response.status_code
        response_reason = response.reason
        return dict(start_time=start_time, response_duration=response_duration,
                    response_code=response_code, response_reason=response_reason)

    def flatten_monitoring_info_dict(self, monitoring_info_dict: dict) -> dict:
        flattened = dict()
        for operation_name, mon_info in monitoring_info_dict.items():
            for metric, value in mon_info.items():
                if metric in ['start_time', 'response_duration', 'response_code', 'response_reason']:
                    if metric == 'start_time' and type(value) == float:
                        value = self.format_timestamp_as_utc(value)
                    flattened[f"{operation_name}.{metric}"] = value
        return flattened

    def get_output_filename_with_run_timestamp(self, output_file_basename:str , suffix: str):
        global run_date_time_stamp
        return f"{output_file_basename}_{run_date_time_stamp}.{suffix}"


    def write_monitoring_info_to_csv(self, monitoring_info_dict: dict, output_file_basename: str) -> None:
        output_filename = self.get_output_filename_with_run_timestamp(output_file_basename, "csv")
        write_header = False if Path(output_filename).exists() else True

        row_info = self.flatten_monitoring_info_dict(monitoring_info_dict)
        with open(output_filename, 'a', newline='') as csvfile:
            fieldnames = sorted(row_info.keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(row_info)


class TerraMethods(MonitoringUtilityMethods):
    terra_info = DeploymentInfo().terra_factory()
    gen3_info = DeploymentInfo().gen3_factory()

    # When run in Terra, this returns the Terra user pet SA token
    def get_terra_user_pet_sa_token(self) -> str:
        import google.auth.transport.requests
        creds, projects = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        token = creds.token
        return token

    def get_fence_token_from_bond(self, terra_user_token: str) -> Tuple[str, dict]:
        headers = {
            'authorization': f"Bearer {terra_user_token}",
            'content-type': "application/json"
        }
        start_time = time.time()
        resp = requests.get(f"https://{self.terra_info.bond_host}/api/link/v1/{self.terra_info.bond_provider}/accesstoken",
                            headers=headers)
        logger.debug(f"Request URL: {resp.request.url}")
        token = resp.json().get('token') if resp.ok else None
        return token, self.monitoring_info(start_time, resp)

    def get_service_account_key_from_bond(self, terra_user_token: str) -> Tuple[dict, dict]:
        headers = {
            'authorization': f"Bearer {terra_user_token}",
            'content-type': "application/json"
        }
        start_time = time.time()
        resp = requests.get(f"https://{self.terra_info.bond_host}/api/link/v1/{self.terra_info.bond_provider}/serviceaccount/key",
                            headers=headers)
        logger.debug(f"Request URL: {resp.request.url}")
        sa_key = resp.json().get('data') if resp.ok else None
        return sa_key, self.monitoring_info(start_time, resp)

    def get_martha_drs_response(self, terra_user_token: str, drs_uri: str = None) -> Tuple[dict, dict]:
        if drs_uri is None:
            drs_uri = self.gen3_info.public_drs_uri

        headers = {
            'authorization': f"Bearer {terra_user_token}",
            'content-type': "application/json"
        }

        # TODO Need to explicitly request an access URL?
        data = json.dumps(dict(url=drs_uri))

        start_time = time.time()
        resp = requests.post(f"https://{self.terra_info.martha_host}/martha_v3/",
                             headers=headers, data=data)
        logger.debug(f"Request URL: {resp.request.url}")
        resp_json = resp.json() if resp.ok else None
        return resp_json, self.monitoring_info(start_time, resp)


class Gen3Methods(MonitoringUtilityMethods):

    gen3_info = DeploymentInfo().gen3_factory()

    def get_gen3_drs_resolution(self, drs_uri: str = None) -> Tuple[dict, dict]:
        if drs_uri is None:
            drs_uri = self.gen3_info.public_drs_uri

        assert drs_uri.startswith("drs://")
        object_id = drs_uri.split(":")[-1]

        headers = {
            'content-type': "application/json"
        }

        start_time = time.time()
        resp = requests.get(f"https://{self.gen3_info.gen3_host}/ga4gh/drs/v1/objects/{object_id}",
                            headers=headers)
        logger.debug(f"Request URL: {resp.request.url}")
        resp_json = resp.json() if resp.ok else None
        return resp_json, self.monitoring_info(start_time, resp)

    @staticmethod
    def _get_drs_access_id(drs_response: dict, cloud_uri_scheme: str) -> Optional[Any]:
        for access_method in drs_response['access_methods']:
            if access_method['type'] == cloud_uri_scheme:
                return access_method['access_id']
        return None

    def get_gen3_drs_access(self, fence_user_token: str, drs_uri: str = None,
                            access_id: str = "gs") -> Tuple[dict, dict]:

        if drs_uri is None:
            drs_uri = self.gen3_info.public_drs_uri

        assert drs_uri.startswith("drs://")
        object_id = drs_uri.split(":")[-1]

        headers = {
            'authorization': f"Bearer {fence_user_token}",
            'content-type': "application/json"
        }

        start_time = time.time()
        resp = requests.get(f"https://{self.gen3_info.gen3_host}/ga4gh/drs/v1/objects/{object_id}/access/{access_id}",
                            headers=headers)
        logger.debug(f"Request URL: {resp.request.url}")
        access_url = resp.json().get('url') if resp.ok else None
        return access_url, self.monitoring_info(start_time, resp)

    def get_fence_userinfo(self, fence_user_token: str):
        headers = {
            'authorization': f"Bearer {fence_user_token}",
            'content-type': "application/json",
            'accept': '*/*'
        }

        start_time = time.time()
        resp = requests.get(f"https://{self.gen3_info.gen3_host}/user/user/", headers=headers)
        logger.debug(f"Request URL: {resp.request.url}")
        resp_json = resp.json() if resp.ok else None
        return resp_json, self.monitoring_info(start_time, resp)


class Scheduler:
    def __init__(self):
        self.stop_run_continuously = None

    @staticmethod
    def run_continuously(interval=1):
        """Continuously run, while executing pending jobs at each
        elapsed time interval.
        @return cease_continuous_run: threading. Event which can
        be set to cease continuous run. Please note that it is
        *intended behavior that run_continuously() does not run
        missed jobs*. For example, if you've registered a job that
        should run every minute, and you set a continuous run
        interval of one hour then your job won't be run 60 times
        at each interval but only once.
        """
        cease_continuous_run = threading.Event()

        class ScheduleThread(threading.Thread):

            def run(self):
                while not cease_continuous_run.is_set():
                    schedule.run_pending()
                    time.sleep(interval)

        continuous_thread = ScheduleThread()
        continuous_thread.start()
        return cease_continuous_run

    @staticmethod
    def run_threaded(job_func):
        job_thread = Thread(target=job_func)
        job_thread.start()

    def start_monitoring(self):
        print("Starting background response time monitoring")
        self.stop_run_continuously = self.run_continuously()

    def stop_monitoring(self):
        print("Stopping background response time monitoring")
        self.stop_run_continuously.set()


class ResponseTimeMonitor(Scheduler):
    interval_seconds = 30
    def catch_exceptions(cancel_on_failure=False):
        def catch_exceptions_decorator(job_func):
            @functools.wraps(job_func)
            def wrapper(*args, **kwargs):
                # noinspection PyBroadException
                try:
                    return job_func(*args, **kwargs)
                except:
                    import traceback
                    print(traceback.format_exc())
                    if cancel_on_failure:
                        return schedule.CancelJob

            return wrapper

        return catch_exceptions_decorator

    class AbstractResponseTimeReporter(ABC):
        def __init__(self, output_filename):
            self.output_filename = output_filename

        @abstractmethod
        def measure_and_report(self):
            pass

    class DrsFlowResponseTimeReporter(AbstractResponseTimeReporter, TerraMethods, Gen3Methods):
        def __init__(self, output_filename):
            super().__init__(output_filename)

        def measure_response_times(self) -> dict:
            monitoring_infos = dict()
            try:
                terra_user_token = self.get_terra_user_pet_sa_token()

                # Get DRS metadata from Gen3 Indexd
                drs_metadata, mon_info = self.get_gen3_drs_resolution()
                monitoring_infos['indexd_get_metadata'] = mon_info

                # Get Fence user token from Bond
                fence_user_token, mon_info = self.get_fence_token_from_bond(terra_user_token)
                monitoring_infos['bond_get_access_token'] = mon_info
                assert fence_user_token is not None, "Failed to get Fence user token."

                # Get service account key from Bond
                sa_key, mon_info = self.get_service_account_key_from_bond(terra_user_token)
                monitoring_infos['bond_get_sa_key'] = mon_info

                # Get signed URL from Fence
                access_url, mon_info = self.get_gen3_drs_access(fence_user_token)
                monitoring_infos['fence_get_signed_url'] = mon_info

            except Exception as ex:
                logger.warning(f"Exception occurred: {ex}")

            return monitoring_infos

        def measure_and_report(self):
            monitoring_infos = self.measure_response_times()

            self.write_monitoring_info_to_csv(monitoring_infos, self.output_filename)


    # class BondResponseTimeReporter(AbstractResponseTimeReporter, TerraMethods):
    #     def __init__(self, output_filename):
    #         super().__init__(output_filename)
    #
    #     def measure_response_time(self) -> Tuple[float, int]:
    #         # Measure Bond response time
    #         terra_user_token = self.get_terra_user_pet_sa_token()
    #         print(f"{datetime.now()} checking bond response time on {currentThread().name}")
    #         start_time = time.time()
    #         headers = {
    #             'authorization': f"Bearer {terra_user_token}",
    #             'content-type': "application/json"
    #         }
    #         resp = requests.get(f"https://{self.terra_info.bond_host}/api/link/v1/{self.terra_info.bond_provider}/accesstoken",
    #                             headers=headers)
    #         duration = time.time() - start_time
    #         return duration, resp.status_code
    #
    #     def measure_and_report(self):
    #         start_time = datetime.now()
    #         response_duration, status_code = self.measure_response_time()
    #         response_duration = round(response_duration, 3)
    #         with open(self.output_filename, "a") as fh:
    #             fh.write(f"{start_time},{response_duration},{status_code}\n")
    #
    # class FenceResponseTimeReporter(AbstractResponseTimeReporter, Gen3Methods):
    #     def __init__(self, output_filename):
    #         super().__init__(output_filename)
    #
    #     # TODO Support configuration of this by project and deployment tier.
    #     FENCE_HOST = "staging.gen3.biodatacatalyst.nhlbi.nih.gov"
    #
    #     def measure_response_time(self) -> Tuple[float, int]:
    #         # Measure Fence health status check response time
    #         print(f"{datetime.now()} checking fence response time on {currentThread().name}")
    #         start_time = time.time()
    #         headers = {
    #             'accept': "*/*"
    #         }
    #         resp = requests.get(f"https://{self.FENCE_HOST}/_status", headers=headers)
    #         duration = time.time() - start_time
    #         return duration, resp.status_code
    #
    #     def measure_and_report(self):
    #         start_time = datetime.now()
    #         response_duration, status_code = self.measure_response_time()
    #         response_duration = round(response_duration, 3)
    #         with open(self.output_filename, "a") as fh:
    #             fh.write(f"{start_time},{response_duration},{status_code}\n")

    @catch_exceptions()
    def check_drs_flow_response_times(self):
        output_filename = "drs_flow_response_times"
        reporter = self.DrsFlowResponseTimeReporter(output_filename)
        reporter.measure_and_report()

    # @catch_exceptions()
    # def check_bond_response_time(self):
    #     output_filename = "bond_fence_token_response_times.csv"
    #     reporter = self.BondResponseTimeReporter(output_filename)
    #     reporter.measure_and_report()
    #
    # @catch_exceptions()
    # def check_fence_response_time(self):
    #     output_filename = "fence_health_status_response_times.csv"
    #     reporter = self.FenceResponseTimeReporter(output_filename)
    #     reporter.measure_and_report()

    def configure_monitoring(self):
        schedule.every(self.interval_seconds).seconds.do(super().run_threaded, self.check_drs_flow_response_times)
        # schedule.every(self.interval_seconds).seconds.do(super().run_threaded, self.check_bond_response_time)
        # schedule.every(self.interval_seconds).seconds.do(super().run_threaded, self.check_fence_response_time)


# Configure and start monitoring
responseTimeMonitor = ResponseTimeMonitor()
responseTimeMonitor.configure_monitoring()
responseTimeMonitor.start_monitoring()

# Run for a while
print("Starting sleep ...")
time.sleep(90)
print("Done sleeping")

# Stop monitoring
responseTimeMonitor.stop_monitoring()