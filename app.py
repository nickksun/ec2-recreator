#!/usr/bin/python

import sys
from time import sleep
import argparse
import boto3
from bcolors import bcolors
import uuid


ec2_client = boto3.client('ec2')

default_tenancy = 'shared'
dry_run = False
launch_only = False

def recreate_instances(source_instance_ids=[]):
    try:
        response = ec2_client.describe_instances(
            InstanceIds=source_instance_ids
        )
        if 'Reservations' in response:
            for reservation in response['Reservations']:
                if 'Instances' not in reservation:
                    raise Exception ('No Instances found')

                for instance in reservation['Instances']:
                    recreate_instance(instance)
            
    except Exception as e:
        print(bcolors.FAIL + "error: %s" % e)

def recreate_instance(instance):
    global dry_run
    global launch_only

    source_instance_id = instance['InstanceId']
    root_volume_id = ''
    block_device_mappings  = []
    for block_device in instance['BlockDeviceMappings']:
        if block_device['DeviceName'] == instance['RootDeviceName']:
            root_volume_id = block_device['Ebs']['VolumeId']

        volume = ec2_client.describe_volumes(
            VolumeIds=[block_device['Ebs']['VolumeId']]
        )
        volume = volume['Volumes'][0]
        block_device_mapping = {
            'DeviceName': volume['Attachments'][0]['Device'],            
            'Ebs': {
                'DeleteOnTermination': volume['Attachments'][0]["DeleteOnTermination"],                
                # 'SnapshotId': 'string',
                'VolumeSize': volume['Size'],
                'VolumeType': volume['VolumeType'],
                'Encrypted': volume['Encrypted']
            },            
        }
        if 'KmsKeyId' in volume:
            block_device_mapping['Ebs']['KmsKeyId'] = volume['KmsKeyId']
        
        if volume['VolumeType'] == 'io1':
            block_device_mapping['Ebs']['Iops'] = volume['Iops']            

        block_device_mappings.append(
            block_device_mapping
        )
            
    
    if not root_volume_id:
        raise Exception ('No root volume found')
        return


    eth0 = instance['NetworkInterfaces'][0]
    

    security_group_ids = []
    for security_group in instance["SecurityGroups"]:
        security_group_ids.append(security_group["GroupId"])

    network_interfaces = []
    for network_interface in instance["NetworkInterfaces"]:
        new_eni = {}
        if "Association" in network_interface and "PublicIp" in network_interface['Association']:
            new_eni['AssociatePublicIpAddress'] = True
        else:
            new_eni['AssociatePublicIpAddress'] = False

        new_eni['DeleteOnTermination'] = network_interface['Attachment']['DeleteOnTermination']
        new_eni['Description'] = network_interface['Description']
        new_eni['DeviceIndex'] = network_interface['Attachment']['DeviceIndex']
        new_eni['Groups'] = []
        for group in network_interface['Groups']:
            new_eni['Groups'].append(group['GroupId'])
        if len(network_interface['Ipv6Addresses']) > 0:
            new_eni['Ipv6AddressCount'] = len(network_interface['Ipv6Addresses'])
            new_eni['Ipv6Addresses'] = network_interface['Ipv6Addresses']
        
        new_eni['SubnetId'] = network_interface['SubnetId']
        new_eni['InterfaceType'] = network_interface['InterfaceType']


    ### Create AMI before launching new instance due to previous AMI may not be accessible

    print(bcolors.OKGREEN + "Creating AMI from instance: %s" % source_instance_id)

    create_image_response = ec2_client.create_image(
        BlockDeviceMappings=block_device_mappings,
        Description='From Recreator',
        DryRun=dry_run,
        InstanceId=source_instance_id,
        Name='recreator-' + str(uuid.uuid4()),
        NoReboot=False
    )
    
    image_id = create_image_response['ImageId']
    while True:
        describe_images_response = ec2_client.describe_images(
            ImageIds=[
                image_id
            ]
        )
        if describe_images_response['Images'][0]['State'] == 'available':
            break

        print(bcolors.WARNING + "Waiting for AMI creation: %s" % image_id)
        sleep(5)

    print(bcolors.OKGREEN + "AMI created from instance: %s" % source_instance_id)


    ### Launch new instance
    print(bcolors.OKGREEN + "Now launching new instance...")
   
    run_instances_response = ec2_client.run_instances(
        BlockDeviceMappings=block_device_mappings,
        ImageId=image_id,
        InstanceType=instance['InstanceType'],
        Ipv6AddressCount=len(eth0['Ipv6Addresses']),
        Ipv6Addresses=eth0['Ipv6Addresses'],
        KeyName=instance['KeyName'],
        MaxCount=1,
        MinCount=1,
        Monitoring={
            'Enabled': True if instance['Monitoring']['State'] is not "disabled" else False
        },
        Placement=instance['Placement'],
    # RamdiskId='string',
        SecurityGroupIds=security_group_ids,
        SubnetId=instance['SubnetId'],
        UserData='' if 'UserData' not in instance else instance['UserData'],
    # AdditionalInfo='string',
    # ClientToken=instance['ClientToken'],
        # DisableApiTermination=True|False,
        DryRun=dry_run,
        EbsOptimized=instance['EbsOptimized'],
        IamInstanceProfile= {} if 'IamInstanceProfile' not in instance else {
            'Arn': instance['IamInstanceProfile']['Arn']
        },
    # InstanceInitiatedShutdownBehavior='stop'|'terminate',    
        NetworkInterfaces=network_interfaces,
    # PrivateIpAddress='string',
    # ElasticGpuSpecification=[
    #     {
    #         'Type': 'string'
    #     },
    # ],
    # ElasticInferenceAccelerators=[
    #     {
    #         'Type': 'string'
    #     },
    # ],
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': instance['Tags']
            },
        ],
    # LaunchTemplate={
    #     'LaunchTemplateId': 'string',
    #     'LaunchTemplateName': 'string',
    #     'Version': 'string'
    # },
    # InstanceMarketOptions={
    #     'MarketType': 'spot',
    #     'SpotOptions': {
    #         'MaxPrice': 'string',
    #         'SpotInstanceType': 'one-time'|'persistent',
    #         'BlockDurationMinutes': 123,
    #         'ValidUntil': datetime(2015, 1, 1),
    #         'InstanceInterruptionBehavior': 'hibernate'|'stop'|'terminate'
    #     }
    # },
    # CreditSpecification={
    #     'CpuCredits': 'string'
    # },
        # CpuOptions=instance['CpuOptions'],
        CapacityReservationSpecification=instance['CapacityReservationSpecification'],
        HibernationOptions={
            'Configured': instance['HibernationOptions']['Configured']
        },
    # LicenseSpecifications=[
    #     {
    #         'LicenseConfigurationArn': 'string'
    #     },
    # ]

    )

    
    new_instance_id = run_instances_response['Instances'][0]['InstanceId']
    print(bcolors.OKGREEN + "New instance launched: %s" % new_instance_id)

    print(bcolors.WARNING + "Waiting for new instance ready: %s" % new_instance_id)

    wait_instance_ready(new_instance_id, 'running')
    
    ## Stop here if "--launch-only" is passed
    if launch_only:
        return
    
    ### Stop both instances
    print(bcolors.WARNING + "Stopping both instances: source: %s new: %s" % (source_instance_id, new_instance_id))

    ec2_client.stop_instances(
        InstanceIds=[
            source_instance_id,
            new_instance_id
        ],
        DryRun=dry_run
    )
    
    wait_instance_ready(source_instance_id, 'stopped')
    wait_instance_ready(new_instance_id, 'stopped')
    
    print(bcolors.OKGREEN + "Both instances are stopped")
    print(bcolors.WARNING + "Now wait for 30 seconds... ") # just in case any instance was not fully stopped

    ### Detach all volumes

    print(bcolors.WARNING + "Detaching volumes from source instance: %s" % source_instance_id)
    source_volumes_result = detach_volumes(source_instance_id)
    print(bcolors.OKGREEN + "Detached volumes from source instance: %s" % source_instance_id)

    print(bcolors.WARNING + "Detaching volumes from new instance: %s" % new_instance_id)
    new_volumes_result = detach_volumes(new_instance_id)
    print(bcolors.OKGREEN + "Detached volumes from new instance: %s" % new_instance_id)

    print(bcolors.WARNING + "Attaching volumes to new instance: %s" % new_instance_id)
    attach_volumes(source_volumes_result, new_instance_id)
    print(bcolors.OKGREEN + "Attached volumes to new instance: %s" % new_instance_id)


    ### Start new instance
    
    print(bcolors.OKGREEN + "Re-start new instance: %s" % new_instance_id)

    ec2_client.start_instances(
        InstanceIds=[
            new_instance_id,
        ],
        DryRun=dry_run
    )
    
    wait_instance_ready(new_instance_id, 'running')

    print(bcolors.OKGREEN + "Hooray!! new instance is running: %s" % new_instance_id)
    
    ## TODO: Add code to DELETE(??) new EBS volumes those detached from new launched instance


def wait_instance_ready(instance_id, desired_status):
    while True:
        print(bcolors.WARNING + "Now waiting for status: %s of instance: %s " % (desired_status, instance_id))

        describe_instances_response = ec2_client.describe_instances(
            InstanceIds=[
                instance_id
            ]
        )
        ready = True
        for instance in describe_instances_response['Reservations'][0]['Instances']:
            if instance['State']['Name'] is not desired_status:
                ready = False
        if ready:
            break
        sleep(5)

def attach_volumes(volumes_result, instance_id):
    print(bcolors.WARNING + "Attaching volumes to: %s" % instance_id)
    for volume in volumes_result:
        ec2_client.attach_volume(
            Device=volume['device_name'],
            InstanceId=instance_id,
            VolumeId=volume['volume_id']
        )
    
    while True:
        print(bcolors.WARNING + "Waiting for attaching volumes to: %s" % instance_id)
        all_attached = True
        volume_ids = []
        for volume in volumes_result:
            volume_ids.append(volume['volume_id'])
        
        describe_volumes_response = ec2_client.describe_volumes(
            VolumeIds=volume_ids
        )
        for volume in describe_volumes_response['Volumes']:
            if volume['State'] != 'in-use':
                all_attached = False
                sleep(5)
        if all_attached:
            break

def detach_volumes(instance_id):
    print(bcolors.WARNING + "Detaching volumes from: %s" % instance_id)

    volumes_result = []
    
    describe_instances_response = ec2_client.describe_instances(
        InstanceIds=[
            instance_id
        ]
    )
    instance = describe_instances_response['Reservations'][0]['Instances'][0]
    block_device_mappings = instance['BlockDeviceMappings']

    for bdm in block_device_mappings:
        if bdm['Ebs']['Status'] == 'attached':
            volume_id = bdm['Ebs']['VolumeId']
            device_name = bdm['DeviceName']
            volumes_result.append({"volume_id": volume_id, "device_name": device_name})
            ec2_client.detach_volume(
                VolumeId=volume_id                
            )

    while True:
        print(bcolors.WARNING + "Waiting for detaching volumes from: %s" % instance_id)
        all_detached = True
        volume_ids = []
        for volume in volumes_result:
            volume_ids.append(volume['volume_id'])
        
        describe_volumes_response = ec2_client.describe_volumes(
            VolumeIds=volume_ids
        )
        for volume in describe_volumes_response['Volumes']:            
            if volume['State'] != 'available':
                all_detached = False
                sleep(5)
        if all_detached:
            break


    return volumes_result


def main():
    parser = argparse.ArgumentParser(description='Re-create an EC2 instance')

    parser.add_argument('--source-instance-ids', nargs='+', default=[], required=True, help='IDs of EC2 instances as source')
    parser.add_argument('--launch-only', nargs='*', default=False,
                        help='Launch without detaching/re-attaching EBS volumes')
    parser.add_argument('--dry-run', default=False,
                        help='Tenancy of new created EC2 instance(s)')
    args = parser.parse_args()    
    
    global dry_run
    global launch_only

    if args.dry_run:
        dry_run = args.dry_run
    
    if args.launch_only != False:
        launch_only = True
    
    recreate_instances(
        source_instance_ids=args.source_instance_ids
        # tenancy=args.tenancy
    )


if __name__ == "__main__":
    main()
