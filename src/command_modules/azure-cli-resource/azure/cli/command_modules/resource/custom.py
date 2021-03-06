# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

# pylint: disable=too-few-public-methods,no-self-use,too-many-arguments

from __future__ import print_function
import json
import os
import uuid

from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.resource.resources.models.resource_group import ResourceGroup
from azure.mgmt.resource.resources.models import GenericResource

from azure.mgmt.resource.policy.models import (PolicyAssignment, PolicyDefinition)

from azure.cli.core.parser import IncorrectUsageError
from azure.cli.core._util import CLIError, get_file_json
import azure.cli.core.azlogging as azlogging
from azure.cli.core.commands.client_factory import get_mgmt_service_client
from azure.cli.core.commands.arm import is_valid_resource_id, parse_resource_id

from ._client_factory import _resource_client_factory, _resource_policy_client_factory

logger = azlogging.get_az_logger(__name__)

def list_resource_groups(tag=None): # pylint: disable=no-self-use
    ''' List resource groups, optionally filtered by a tag.
    :param str tag:tag to filter by in 'key[=value]' format
    '''
    rcf = _resource_client_factory()

    filters = []
    if tag:
        key = list(tag.keys())[0]
        filters.append("tagname eq '{}'".format(key))
        filters.append("tagvalue eq '{}'".format(tag[key]))

    filter_text = ' and '.join(filters) if len(filters) > 0 else None

    groups = rcf.resource_groups.list(filter=filter_text)
    return list(groups)

def create_resource_group(resource_group_name, location, tags=None):
    ''' Create a new resource group.
    :param str resource_group_name:the desired resource group name
    :param str location:the resource group location
    :param str tags:tags in 'a=b c' format
    '''
    rcf = _resource_client_factory()

    if rcf.resource_groups.check_existence(resource_group_name):
        raise CLIError('resource group {} already exists'.format(resource_group_name))
    parameters = ResourceGroup(
        location=location,
        tags=tags
    )
    return rcf.resource_groups.create_or_update(resource_group_name, parameters)

def export_group_as_template(
        resource_group_name, include_comments=False, include_parameter_default_value=False):
    '''Captures a resource group as a template.
    :param str resource_group_name:the name of the resoruce group.
    :param bool include_comments:export template with comments.
    :param bool include_parameter_default_value: export template parameter with default value.
    '''
    rcf = _resource_client_factory()

    export_options = []
    if include_comments:
        export_options.append('IncludeComments')
    if include_parameter_default_value:
        export_options.append('IncludeParameterDefaultValue')

    options = ','.join(export_options) if export_options else None

    result = rcf.resource_groups.export_template(resource_group_name, '*', options=options)
    #pylint: disable=no-member
    # On error, server still returns 200, with details in the error attribute
    if result.error:
        error = result.error
        if (hasattr(error, 'details') and error.details and
                hasattr(error.details[0], 'message')):
            error = error.details[0].message
        raise CLIError(error)

    print(json.dumps(result.template, indent=2))

def deploy_arm_template(
        resource_group_name, template_file=None, template_uri=None, deployment_name=None,
        parameters=None, mode='incremental', no_wait=False):
    return _deploy_arm_template_core(resource_group_name, template_file, template_uri,
                                     deployment_name, parameters, mode, no_wait=no_wait)

def validate_arm_template(resource_group_name, template_file=None, template_uri=None,
                          parameters=None, mode='incremental'):
    return _deploy_arm_template_core(resource_group_name, template_file, template_uri,
                                     'deployment_dry_run', parameters, mode, validate_only=True)

def _deploy_arm_template_core(resource_group_name, template_file=None, template_uri=None,
                              deployment_name=None, parameters=None, mode='incremental',
                              validate_only=False, no_wait=False):
    from azure.mgmt.resource.resources.models import DeploymentProperties, TemplateLink

    if bool(template_uri) == bool(template_file):
        raise CLIError('please provide either template file path or uri, but not both')

    if parameters:
        parameters = json.loads(parameters)
        if parameters:
            parameters = parameters.get('parameters', parameters)

    template = None
    template_link = None
    if template_uri:
        template_link = TemplateLink(uri=template_uri)
    else:
        template = get_file_json(template_file)

    properties = DeploymentProperties(template=template, template_link=template_link,
                                      parameters=parameters, mode=mode)

    smc = get_mgmt_service_client(ResourceManagementClient)
    if validate_only:
        return smc.deployments.validate(resource_group_name, deployment_name,
                                        properties, raw=no_wait)
    else:
        return smc.deployments.create_or_update(resource_group_name, deployment_name,
                                                properties, raw=no_wait)


def export_deployment_as_template(resource_group_name, deployment_name):
    smc = get_mgmt_service_client(ResourceManagementClient)
    result = smc.deployments.export_template(resource_group_name, deployment_name)
    print(json.dumps(result.template, indent=2))#pylint: disable=no-member

def show_resource(resource_group_name=None, resource_provider_namespace=None,
                  parent_resource_path=None, resource_type=None, resource_name=None,
                  resource_id=None, api_version=None):
    res = _ResourceUtils(resource_group_name, resource_provider_namespace,
                         parent_resource_path, resource_type, resource_name,
                         resource_id, api_version)
    return res.get_resource()

def delete_resource(resource_group_name=None, resource_provider_namespace=None,
                    parent_resource_path=None, resource_type=None, resource_name=None,
                    resource_id=None, api_version=None):
    res = _ResourceUtils(resource_group_name, resource_provider_namespace,
                         parent_resource_path, resource_type, resource_name,
                         resource_id, api_version)
    res.delete()

def tag_resource(tags, resource_group_name=None, resource_provider_namespace=None,
                 parent_resource_path=None, resource_type=None, resource_name=None,
                 resource_id=None, api_version=None):
    ''' Updates the tags on an existing resource. To clear tags, specify the --tag option
    without anything else. '''
    res = _ResourceUtils(resource_group_name, resource_provider_namespace,
                         parent_resource_path, resource_type, resource_name,
                         resource_id, api_version)
    return res.tag(tags)

def get_deployment_operations(client, resource_group_name, deployment_name, operation_ids):
    """get a deployment's operation.
    """
    result = []
    for op_id in operation_ids:
        dep = client.get(resource_group_name, deployment_name, op_id)
        result.append(dep)
    return result

def list_resources(resource_group_name=None, resource_provider_namespace=None,
                   resource_type=None, name=None, tag=None, location=None):
    rcf = _resource_client_factory()
    odata_filter = _list_resources_odata_filter_builder(resource_group_name,
                                                        resource_provider_namespace,
                                                        resource_type, name, tag, location)
    resources = rcf.resources.list(filter=odata_filter)
    return list(resources)

def _list_resources_odata_filter_builder(resource_group_name=None,
                                         resource_provider_namespace=None, resource_type=None,
                                         name=None, tag=None, location=None):
    '''Build up OData filter string from parameters
    '''
    filters = []

    if resource_group_name:
        filters.append("resourceGroup eq '{}'".format(resource_group_name))

    if name:
        filters.append("name eq '{}'".format(name))

    if location:
        filters.append("location eq '{}'".format(location))

    if resource_type:
        if resource_provider_namespace:
            f = "'{}/{}'".format(resource_provider_namespace, resource_type)
        else:
            #assume resource_type is <namespace>/<type>. The worst is to get a server error
            f = "'{}'".format(resource_type)
        filters.append("resourceType eq " + f)

    if tag:
        if name or location:
            raise IncorrectUsageError('you cannot use the tag filter with other filters')

        tag_name = list(tag.keys())[0] if isinstance(tag, dict) else tag
        tag_value = tag[tag_name] if isinstance(tag, dict) else ''
        if tag_name:
            if tag_name[-1] == '*':
                filters.append("startswith(tagname, '%s')" % tag_name[0:-1])
            else:
                filters.append("tagname eq '%s'" % tag_name)
                if tag_value != '':
                    filters.append("tagvalue eq '%s'" % tag_value)
    return ' and '.join(filters)

def get_providers_completion_list(prefix, **kwargs): #pylint: disable=unused-argument
    rcf = _resource_client_factory()
    result = rcf.providers.list()
    return [r.namespace for r in result]

def get_resource_types_completion_list(prefix, **kwargs): #pylint: disable=unused-argument
    rcf = _resource_client_factory()
    result = rcf.providers.list()
    types = []
    for p in list(result):
        for r in p.resource_types:
            types.append(p.namespace + '/' + r.resource_type)
    return types

def register_provider(resource_provider_namespace):
    _update_provider(resource_provider_namespace, registering=True)

def unregister_provider(resource_provider_namespace):
    _update_provider(resource_provider_namespace, registering=False)

def _update_provider(namespace, registering):
    rcf = _resource_client_factory()
    if registering:
        rcf.providers.register(namespace)
    else:
        rcf.providers.unregister(namespace)

    #timeout'd, normal for resources with many regions, but let users know.
    action = 'Registering' if registering else 'Unregistering'
    msg_template = '%s is still on-going. You can monitor using \'az provider show -n %s\''
    logger.warning(msg_template, action, namespace)

def move_resource(ids, destination_group, destination_subscription_id=None):
    '''Moves resources from one resource group to another(can be under different subscription)

    :param ids: the space separated resource ids to be moved
    :param destination_group: the destination resource group name
    :param destination_subscription_id: the destination subscription identifier
    '''
    from azure.cli.core.commands.arm import resource_id

    #verify all resource ids are valid and under the same group
    resources = []
    for i in ids:
        if is_valid_resource_id(i):
            resources.append(parse_resource_id(i))
        else:
            raise CLIError('Invalid id "{}", as it has no group or subscription field'.format(i))

    if len(set([r['subscription'] for r in resources])) > 1:
        raise CLIError('All resources should be under the same subscription')
    if len(set([r['resource_group'] for r in resources])) > 1:
        raise CLIError('All resources should be under the same group')

    rcf = _resource_client_factory()
    target = resource_id(subscription=(destination_subscription_id or rcf.config.subscription_id),
                         resource_group=destination_group)

    return rcf.resources.move_resources(resources[0]['resource_group'], ids, target)

def list_features(client, resource_provider_namespace=None):
    if resource_provider_namespace:
        return client.list(resource_provider_namespace=resource_provider_namespace)
    else:
        return client.list_all()

def create_policy_assignment(policy, name=None, display_name=None,
                             resource_group_name=None, scope=None):
    policy_client = _resource_policy_client_factory()
    scope = _build_policy_scope(policy_client.config.subscription_id,
                                resource_group_name, scope)
    policy_id = _resolve_policy_id(policy, policy_client)
    assignment = PolicyAssignment(display_name, policy_id, scope)
    return policy_client.policy_assignments.create(scope,
                                                   name or uuid.uuid4(),
                                                   assignment)

def delete_policy_assignment(name, resource_group_name=None, scope=None):
    policy_client = _resource_policy_client_factory()
    scope = _build_policy_scope(policy_client.config.subscription_id,
                                resource_group_name, scope)
    policy_client.policy_assignments.delete(scope, name)

def show_policy_assignment(name, resource_group_name=None, scope=None):
    policy_client = _resource_policy_client_factory()
    scope = _build_policy_scope(policy_client.config.subscription_id,
                                resource_group_name, scope)
    return policy_client.policy_assignments.get(scope, name)

def list_policy_assignment(disable_scope_strict_match=None, resource_group_name=None, scope=None):
    policy_client = _resource_policy_client_factory()
    if scope and not is_valid_resource_id(scope):
        parts = scope.strip('/').split('/')
        if len(parts) == 4:
            resource_group_name = parts[3]
        elif len(parts) == 2:
            #rarely used, but still verify
            if parts[1].lower() != policy_client.config.subscription_id.lower():
                raise CLIError("Please use current active subscription's id")
        else:
            err = "Invalid scope '{}', it should point to a resource group or a resource"
            raise CLIError(err.format(scope))
        scope = None

    _scope = _build_policy_scope(policy_client.config.subscription_id,
                                 resource_group_name, scope)
    if resource_group_name:
        result = policy_client.policy_assignments.list_for_resource_group(resource_group_name)
    elif scope:
        #pylint: disable=redefined-builtin
        id = parse_resource_id(scope)
        parent_resource_path = '' if not id.get('child_name') else (id['type'] + '/' + id['name'])
        resource_type = id.get('child_type') or id['type']
        resource_name = id.get('child_name') or id['name']
        result = policy_client.policy_assignments.list_for_resource(
            id['resource_group'], id['namespace'],
            parent_resource_path, resource_type, resource_name)
    else:
        result = policy_client.policy_assignments.list()

    if not disable_scope_strict_match:
        result = [i for i in result if _scope.lower() == i.scope.lower()]

    return result

def _build_policy_scope(subscription_id, resource_group_name, scope):
    subscription_scope = '/subscriptions/' + subscription_id
    if scope:
        if resource_group_name:
            err = "Resource group '{}' is redundant because 'scope' is supplied"
            raise CLIError(err.format(resource_group_name))
    elif resource_group_name:
        scope = subscription_scope + '/resourceGroups/' + resource_group_name
    else:
        scope = subscription_scope
    return scope

def _resolve_policy_id(policy, client):
    policy_id = policy
    if not is_valid_resource_id(policy):
        policy_def = client.policy_definitions.get(policy)
        policy_id = policy_def.id
    return policy_id

def create_policy_definition(name, rules, display_name=None, description=None):
    if os.path.exists(rules):
        rules = get_file_json(rules)
    else:
        rules = json.loads(rules)

    policy_client = _resource_policy_client_factory()
    parameters = PolicyDefinition(policy_rule=rules, description=description,
                                  display_name=display_name)
    return policy_client.policy_definitions.create_or_update(name, parameters)

def update_policy_definition(policy_definition_name, rules=None,
                             display_name=None, description=None):
    if rules is not None:
        if os.path.exists(rules):
            rules = get_file_json(rules)
        else:
            rules = json.loads(rules)

    policy_client = _resource_policy_client_factory()
    definition = policy_client.policy_definitions.get(policy_definition_name)
    #pylint: disable=line-too-long,no-member
    parameters = PolicyDefinition(policy_rule=rules if rules is not None else definition.policy_rule,
                                  description=description if description is not None else definition.description,
                                  display_name=display_name if display_name is not None else definition.display_name)
    return policy_client.policy_definitions.create_or_update(policy_definition_name, parameters)

def get_policy_completion_list(prefix, **kwargs):#pylint: disable=unused-argument
    policy_client = _resource_policy_client_factory()
    result = policy_client.policy_definitions.list()
    return [i.name for i in result]

def get_policy_assignment_completion_list(prefix, **kwargs):#pylint: disable=unused-argument
    policy_client = _resource_policy_client_factory()
    result = policy_client.policy_assignments.list()
    return [i.name for i in result]

class _ResourceUtils(object): #pylint: disable=too-many-instance-attributes
    def __init__(self, resource_group_name=None, resource_provider_namespace=None,
                 parent_resource_path=None, resource_type=None, resource_name=None,
                 resource_id=None, api_version=None, rcf=None):
        if bool(resource_id) == bool(resource_group_name or resource_type or
                                     parent_resource_path or resource_provider_namespace or
                                     resource_name):
            raise IncorrectUsageError(
                "(--id ID | --resource-group RG --name NAME --namespace NAMESPACE --resource-type TYPE -n NAME)") #pylint: disable=line-too-long

        #if the resouce_type is in format 'namespace/type' split it.
        #(we don't have to do this, but commands like 'vm show' returns such values)
        if resource_type and not resource_provider_namespace and not parent_resource_path:
            parts = resource_type.split('/')
            if len(parts) > 1:
                resource_provider_namespace = parts[0]
                resource_type = parts[1]

        self.rcf = rcf or _resource_client_factory()
        if api_version is None:
            if resource_id:
                api_version = _ResourceUtils._resolve_api_version_by_id(self.rcf, resource_id)
            else:
                api_version = _ResourceUtils._resolve_api_version(self.rcf,
                                                                  resource_provider_namespace,
                                                                  parent_resource_path,
                                                                  resource_type)

        self.resource_group_name = resource_group_name
        self.resource_provider_namespace = resource_provider_namespace
        self.parent_resource_path = parent_resource_path
        self.resource_type = resource_type
        self.resource_name = resource_name
        self.resource_id = resource_id
        self.api_version = api_version

    def get_resource(self):
        if self.resource_id:
            resource = self.rcf.resources.get_by_id(self.resource_id, self.api_version)
        else:
            resource = self.rcf.resources.get(self.resource_group_name,
                                              self.resource_provider_namespace,
                                              self.parent_resource_path or '',
                                              self.resource_type,
                                              self.resource_name,
                                              self.api_version)
        return resource

    def delete(self):
        if self.resource_id:
            self.rcf.resources.delete_by_id(self.resource_id, self.api_version)
        else:
            self.rcf.resources.delete(self.resource_group_name,
                                      self.resource_provider_namespace,
                                      self.parent_resource_path or '',
                                      self.resource_type,
                                      self.resource_name,
                                      self.api_version)

    def tag(self, tags):
        resource = self.get_resource()
        # pylint: disable=no-member
        parameters = GenericResource(
            location=resource.location,
            tags=tags,
            plan=resource.plan,
            properties=resource.properties,
            kind=resource.kind,
            managed_by=resource.managed_by,
            sku=resource.sku,
            identity=resource.identity)

        if self.resource_id:
            return self.rcf.resources.create_or_update_by_id(self.resource_id, self.api_version,
                                                             parameters)
        else:
            return self.rcf.resources.create_or_update(
                self.resource_group_name,
                self.resource_provider_namespace,
                self.parent_resource_path or '',
                self.resource_type,
                self.resource_name,
                self.api_version,
                parameters)

    @staticmethod
    def _resolve_api_version(rcf, resource_provider_namespace, parent_resource_path, resource_type):
        provider = rcf.providers.get(resource_provider_namespace)

        #If available, we will use parent resource's api-version
        resource_type_str = (parent_resource_path.split('/')[0]
                             if parent_resource_path else resource_type)

        rt = [t for t in provider.resource_types
              if t.resource_type.lower() == resource_type_str.lower()]
        if not rt:
            raise IncorrectUsageError('Resource type {} not found.'
                                      .format(resource_type_str))
        if len(rt) == 1 and rt[0].api_versions:
            npv = [v for v in rt[0].api_versions if 'preview' not in v.lower()]
            return npv[0] if npv else rt[0].api_versions[0]
        else:
            raise IncorrectUsageError(
                'API version is required and could not be resolved for resource {}'
                .format(resource_type))

    @staticmethod
    def _resolve_api_version_by_id(rcf, resource_id):
        parts = parse_resource_id(resource_id)
        if parts.get('grandchild_type'):
            parent = (parts['type'] + '/' +  parts['name'] + '/' +
                      parts['child_type'] + '/' + parts['child_name'])
            resource_type = parts['grandchild_type']
        elif parts.get('child_type'):
            parent = parts['type'] + '/' +  parts['name']
            resource_type = parts['child_type']
        else:
            parent = None
            resource_type = parts['type']

        return _ResourceUtils._resolve_api_version(rcf, parts['namespace'], parent, resource_type)
