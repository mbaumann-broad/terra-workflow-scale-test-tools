"""Terra Workflow Information
This script/module provides a simple data access object (DAO) for information about a Terra workflow.
It is primarily designed to be imported and used in Jupyter Notebooks.
"""

from datetime import datetime
import json
import time

import requests


class WorkflowDAO:
    """ Workflow information data access class
    """

    def __init__(self, terra_deployment_tier, workspace_namespace: str, workspace_name: str, wf_submission_id: str):
        self.terra_deployment_tier = terra_deployment_tier
        self.workspace_namespace = workspace_namespace
        self.workspace_name = workspace_name
        self.wf_submission_id = wf_submission_id
        self.firecloud_api_url = \
            f"https://firecloud-orchestration.dsde-{self.terra_deployment_tier.lower()}.broadinstitute.org"
        self.workflow_info: dict = None

    @staticmethod
    def _get_terra_user_token() -> str:
        import google.auth.transport.requests
        creds, projects = google.auth.default()
        creds.refresh(google.auth.transport.requests.Request())
        token = creds.token
        return token

    # TODO Make this more efficient
    def update(self):
        terra_user_token = self._get_terra_user_token()

        headers = {
            'authorization': f"Bearer {terra_user_token}",
            'content-type': "application/json"
        }

        resp = requests.get(f"{self.firecloud_api_url}/api/workspaces/{self.workspace_namespace}/{self.workspace_name}/submissions/{self.wf_submission_id}",
                            headers=headers)
        # print(f"Request URL: {resp.request.url}")  # Debugging
        resp.raise_for_status()
        self.workflow_info = resp.json() if resp.ok else None

    def get_workflow_info(self) -> dict:
        if self.workflow_info is None:
            self.update()
        return self.workflow_info

    def get_submission_status(self) -> str:
        return self.get_workflow_info()['status']

    def is_in_process(self) -> bool:
        in_process_status_list = ["Queued", "Submitted", "Running"]
        return self.get_workflow_info()['status'] in in_process_status_list

    def get_submission_time(self, strftime_format_string: str = None):
        submission_date = self.get_workflow_info()['submissionDate']
        if strftime_format_string is not None:
            # Convert submissionDate format to the specific ISO format supported by `fromisoformat`
            iso_submission_date = submission_date.replace('Z', '+00:00')
            return datetime.fromisoformat(iso_submission_date).strftime(strftime_format_string)
        else:
            return submission_date

    def get_method_configuration_display_name(self) -> str:
        return f"{self.get_workflow_info()['methodConfigurationNamespace']}/{self.get_workflow_info()['methodConfigurationName']}"

    def get_submitter(self) -> str:
        return self.get_workflow_info()['submitter']

    def get_submission_id(self) -> str:
        return self.get_workflow_info()['submissionId']

    def get_submission_entity_display_name(self) -> str:
        submission_entity = self.get_workflow_info()['submissionEntity']
        return f"{submission_entity['entityType']}:{submission_entity['entityName']}"

    def get_use_call_cache(self) -> str:
        return self.get_workflow_info()['useCallCache']

    def get_user_comment(self) -> str:
        return self.get_workflow_info()['userComment']

    def get_workflow_summary_display_string(self) -> str:
        return "\n".join([f"Method Configuration: {self.get_method_configuration_display_name()}",
                          f"Submitter: {self.get_submitter()}",
                          # TODO Display in in Eastern and Pacific time also
                          f"Submitted: {self.get_submission_time('%Y/%m/%d %H:%M:%S')} UTC",
                          f"Submission Id: {self.get_submission_id()}",
                          f"Submission Entity: {self.get_submission_entity_display_name()}",
                          f"Use Call Cache: {self.get_use_call_cache()}",
                          f"Submission Status: {self.get_submission_status()}",
                          f"User Comment: {self.get_user_comment()}"])


def wait_for_workflow_to_complete(workflow_dao: WorkflowDAO) -> None:
    sleep_seconds = 30
    while workflow_dao.is_in_process():
        print(f"Submission status: {workflow_dao.get_submission_status()}")
        print(f"Sleeping for {sleep_seconds} seconds ...")
        time.sleep(sleep_seconds)
        print("Getting current submission status ... ")
        workflow_dao.update()
    print(f"Final Submission status: {workflow_dao.get_submission_status()}")


if __name__ == "__main__":
    TERRA_DEPLOYMENT_TIER = "ALPHA"
    WORKSPACE_NAMESPACE = "drs-billing-project"
    WORKSPACE_NAME = "DRS Data Access Scale Testing - Alpha"
    WF_SUBMISSION_ID = "32f74aa9-7779-4d28-aec7-641d26307beb"

    workflow_dao = WorkflowDAO(TERRA_DEPLOYMENT_TIER, WORKSPACE_NAMESPACE, WORKSPACE_NAME, WF_SUBMISSION_ID)

    workflow_info = workflow_dao.get_workflow_info()
    print(json.dumps(workflow_info, indent=4))
    print(f"Workflow in process: {workflow_dao.is_in_process()}")
    print(f"Submission time: {workflow_dao.get_submission_time()}")
    print(f"Submission time: {workflow_dao.get_submission_time('%Y/%m/%d %H:%M:%S')}")
    print(f"Submission Summary:\n{workflow_dao.get_workflow_summary_display_string()}")