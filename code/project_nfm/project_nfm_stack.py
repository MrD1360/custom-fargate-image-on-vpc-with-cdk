from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    Stack, NestedStack,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_docdb as docdb,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_transfer as transfer,
    RemovalPolicy
)

from constructs import Construct


class ProjectNfmStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, project_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        env_name = self.node.try_get_context('env_name')
        env = self.node.try_get_context(env_name).get('env', None)

        if env is None:
            raise Exception("no environment given")

        # starting stack definition

        # creating network stack
        Network = NetworkStack(self, f"networkStack-{construct_id}", project_name=project_name, env_name=env)
        vpc = Network.vpc

        # adding s3 gateway
        vpc.add_gateway_endpoint(f"{project_name}-{env}-S3Endpoint",
                                 service=ec2.GatewayVpcEndpointAwsService.S3,
                                 subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED)])

        # defining security group

        sg_endpoints = ec2.SecurityGroup(
            self,
            id=f"{project_name}-{env}-endpoint_sg",
            vpc=vpc,
            security_group_name=f"{project_name}-{env}-endpoint_sg"
        )

        sg_endpoints.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443)
        )

        sg_endpoints.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80)
        )

        sg_alb_pb = ec2.SecurityGroup(
            self,
            id=f"{project_name}-{env}-sg_alb_internter_facing",
            vpc=vpc,
            security_group_name=f"{project_name}-{env}-sg_alb_pub"
        )

        sg_alb_pb.add_ingress_rule(
            peer=ec2.Peer.ipv4('customIP/32'),
            connection=ec2.Port.tcp(443)
        )

        sg_customer_app_service = ec2.SecurityGroup(
            self,
            id=f"{project_name}-{env}-sg_customer_app_service",
            vpc=vpc,
            security_group_name=f"{project_name}-{env}-sg_customer_app_service"
        )

        sg_customer_app_service.connections.allow_to(sg_alb_pb, ec2.Port.tcp(433))

        sg_document_service = ec2.SecurityGroup(
            self,
            id=f"{project_name}-{env}-docdb_server_sg",
            vpc=vpc,
            security_group_name=f"{project_name}-{env}-docdb_server_sg"
        )

        sg_document_service.connections.allow_to(sg_customer_app_service, ec2.Port.tcp(433))

        # vpc endpoint

        ec2.InterfaceVpcEndpoint(self, f"{project_name}-{env}-ecr-dkr-vpcendpoint",
                                 vpc=vpc,
                                 service=ec2.InterfaceVpcEndpointService("com.amazonaws.eu-central-1.ecr.dkr", 443),
                                 subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                 security_groups=[sg_endpoints],
                                 private_dns_enabled=True

                                 )

        ec2.InterfaceVpcEndpoint(self, f"{project_name}-{env}-ecr-api-vpcendpoint",
                                 vpc=vpc,
                                 service=ec2.InterfaceVpcEndpointService("com.amazonaws.eu-central-1.ecr.api", 443),
                                 subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                 security_groups=[sg_endpoints],
                                 private_dns_enabled=True
                                 )

        ec2.InterfaceVpcEndpoint(self, f"{project_name}-{env}-secretsmanager",
                                 vpc=vpc,
                                 service=ec2.InterfaceVpcEndpointService("com.amazonaws.eu-central-1.secretsmanager",
                                                                         443),
                                 subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                 security_groups=[sg_endpoints],
                                 private_dns_enabled=True
                                 )

        # deinfing cluster

        cluster = ecs.Cluster(self, f"{project_name}-{env}-ecsCluster", vpc=vpc,
                              cluster_name=f"{project_name}-{env}-ecsCluster")

        # task role

        task_role_fargate = iam.Role(self, f"{project_name}-{env}-task_role_fargate",
                                     role_name=f"{project_name}-{env}-task_role_fargate",
                                     assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                                     description="task role"
                                     )

        task_role_fargate.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonEC2ContainerRegistryReadOnly"))  # "service-role/AmazonEC2ContainerRegistryReadOnly"

        """
        policy as follow:
        
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:GetAuthorizationToken",
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:GetRepositoryPolicy",
                        "ecr:DescribeRepositories",
                        "ecr:ListImages",
                        "ecr:DescribeImages",
                        "ecr:BatchGetImage",
                        "ecr:GetLifecyclePolicy",
                        "ecr:GetLifecyclePolicyPreview",
                        "ecr:ListTagsForResource",
                        "ecr:DescribeImageScanFindings"
                    ],
                    "Resource": "*"
                }
            ]
        }
        """

        # task definition

        fargate_task_definition = ecs.FargateTaskDefinition(self, f"{project_name}-{env}-fargateTaskDef",
                                                            memory_limit_mib=512,
                                                            task_role=task_role_fargate,
                                                            execution_role=task_role_fargate
                                                            # cpu=256
                                                            )

        applucationcontainer = fargate_task_definition.add_container(f"{project_name}-{env}-applicationcontainer",
                                                                     # Use an image from ECR
                                                                     image=ecs.ContainerImage.from_registry(
                                                                         "<accountID>.dkr.ecr.<region>.amazonaws.com/<repo>:<imgversion>")
                                                                     )

        applucationcontainer.add_port_mappings(ecs.PortMapping(container_port=80, protocol=ecs.Protocol.TCP))

        # defining services
        service = ecs.FargateService(self, f"{project_name}-{env}-FargateService",
                                     service_name=f"{project_name}-{env}-FargateService",
                                     cluster=cluster,
                                     task_definition=fargate_task_definition,
                                     security_groups=[sg_customer_app_service], desired_count=1,
                                     vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED))

        # defining load balancer
        lb = elbv2.ApplicationLoadBalancer(self, f"{project_name}-{env}-frontALB",
                                           load_balancer_name=f"{project_name}-{env}-frontALB", vpc=vpc,
                                           internet_facing=True,
                                           security_group=sg_alb_pb,
                                           vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
                                           )

        listener_alb = lb.add_listener(f"{project_name}-{env}-alb_listener", port=80,
                                       protocol=elbv2.ApplicationProtocol.HTTP)

        fargate_service = service.register_load_balancer_targets(
            ecs.EcsTarget(container_name=f"{project_name}-{env}-applicationcontainer", container_port=80,
                          listener=ecs.ListenerConfig.application_listener(
                              listener_alb,
                              protocol=elbv2.ApplicationProtocol.HTTP),
                          new_target_group_id=f"{project_name}-{env}-fargatetargate"))

        # define documentDB cluster
        # db = DocumentDBCluster(self, f"documentDB-{construct_id}", networkstack=Network,
        #                        security_group=sg_document_service, env_name=env,
        #                        project_name=project_name)

        # s3 data to save data
        s3.Bucket(self, f"{project_name}-{env}-data-storage",
                  # bucket_name=f"{project_name}-{env}--data-storage",
                  object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
                  block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                  removal_policy=RemovalPolicy.DESTROY,
                  encryption=s3.BucketEncryption.S3_MANAGED
                  )

        # define lambda and api gateway for authentication
        authlambda = _lambda.Function(
            self, f"{project_name}-{env}-authlambda",
            runtime=_lambda.Runtime.PYTHON_3_9,
            code=_lambda.Code.from_asset("resources"),
            handler="authlambda.handler",
            function_name=f"{project_name}-{env}-authlambda"
        )

        apigw.LambdaRestApi(
            self, f"{project_name}-{env}-apigwEndpoint",
            handler=authlambda,
        )

        # ftp server definition (to be defined)
        # transfer.CfnServer()
        # transfer.CfnUser()


class NetworkStack(NestedStack):
    def __init__(self, scope: Construct, construct_id: str, project_name: str, env_name=None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # defining vpc
        self.public_subnet = ec2.SubnetConfiguration(name=f"{project_name}-{env_name}-publicsubnet",
                                                     subnet_type=ec2.SubnetType.PUBLIC,
                                                     cidr_mask=28)
        self.private_subnet = ec2.SubnetConfiguration(name=f"{project_name}-{env_name}-privatesubnet",
                                                      subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                                                      cidr_mask=28)

        self.subnets = [self.public_subnet, self.private_subnet]

        self.vpc = ec2.Vpc(self, f"{project_name}-{env_name}-customVPC", subnet_configuration=self.subnets, max_azs=2)


class DocumentDBCluster(NestedStack):
    def __init__(self, scope: Construct, construct_id: str, project_name: str, networkstack: NetworkStack,
                 security_group: ec2.SecurityGroup, env_name=None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # defining mongodb cluster
        self.docdb_cluster = docdb.DatabaseCluster(self, f"{project_name}-{env_name}-mongoCluster",
                                                   master_user=docdb.Login(
                                                       username="chosenuser",  # NOTE: 'admin' is reserved by DocumentDB
                                                       #exclude_characters="", # @/:",  # optional, defaults to the set ""@/" and is also used for eventually created rotations
                                                       secret_name=f"{project_name}-{env_name}-/usercluster/docdb/chosenuser"),
                                                   instance_type=ec2.InstanceType.of(ec2.InstanceClass.T2,
                                                                                     ec2.InstanceSize.MICRO),
                                                   vpc_subnets=ec2.SubnetSelection(
                                                       subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
                                                   vpc=networkstack.vpc,
                                                   security_group=security_group,  # sg_document_service,
                                                   db_cluster_name=f"{project_name}-{env_name}-documentDBcluster"
                                                   )
