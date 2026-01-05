"""An AWS Python Pulumi program"""

import pulumi
import pulumi_awsx as awsx
import pulumi_docker as docker
from pulumi_aws import acm, ec2, ecr, ecs, iam, lb, route53, s3


# Create a VPC with public and private subnets across 2 availability zones
vpc = awsx.ec2.Vpc(
    "streamlit-vpc",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    cidr_block="10.0.0.0/16",
    number_of_availability_zones=2,
    nat_gateways=awsx.ec2.NatGatewayConfigurationArgs(
        strategy=awsx.ec2.NatGatewayStrategy.SINGLE
    ),
)

# Create an ECR repository to store the Docker image
ecr_repo = ecr.Repository(
    "streamlit-app-repo",
    image_scanning_configuration=ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True
    ),
    force_delete=True,  # Allow deletion even if images exist (for dev)
)

# Get ECR authorization credentials
ecr_auth_token = ecr.get_authorization_token_output()

# Build and push Docker image to ECR
app_image = docker.Image(
    "streamlit-app-image",
    build=docker.DockerBuildArgs(
        context="../app",  # Path to the app directory with Dockerfile
        dockerfile="../app/Dockerfile",
        platform="linux/amd64",  # Ensure compatibility with Fargate
    ),
    image_name=ecr_repo.repository_url,
    registry=docker.RegistryArgs(
        server=ecr_repo.repository_url,
        username=ecr_auth_token.user_name,
        password=ecr_auth_token.password,
    ),
)

# Create an ECS cluster
cluster = ecs.Cluster("streamlit-cluster")

# Create IAM role for ECS task execution
task_exec_role = iam.Role(
    "streamlit-task-exec-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {
                "Service": "ecs-tasks.amazonaws.com"
            }
        }]
    }""",
)

# Attach the AWS managed policy for ECS task execution
task_exec_policy_attachment = iam.RolePolicyAttachment(
    "streamlit-task-exec-policy",
    role=task_exec_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
)

# Add CloudWatch Logs permissions for log group creation
cloudwatch_logs_policy = iam.RolePolicy(
    "streamlit-task-exec-logs-policy",
    role=task_exec_role.id,
    policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        }]
    }""",
)

# Create IAM role for ECS task (application runtime)
task_role = iam.Role(
    "streamlit-task-role",
    assume_role_policy="""{
        "Version": "2012-10-17",
        "Statement": [{
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {
                "Service": "ecs-tasks.amazonaws.com"
            }
        }]
    }""",
)

# Create Fargate task definition
task_definition = ecs.TaskDefinition(
    "streamlit-task",
    family="streamlit-app",
    cpu="256",  # 0.25 vCPU
    memory="512",  # 512 MB
    network_mode="awsvpc",
    requires_compatibilities=["FARGATE"],
    execution_role_arn=task_exec_role.arn,
    task_role_arn=task_role.arn,
    container_definitions=pulumi.Output.json_dumps(
        [
            {
                "name": "streamlit-container",
                "image": app_image.image_name,
                "portMappings": [
                    {
                        "containerPort": 8501,
                        "protocol": "tcp",
                    }
                ],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": "/ecs/streamlit-app",
                        "awslogs-region": "us-east-1",
                        "awslogs-stream-prefix": "ecs",
                        "awslogs-create-group": "true",
                    },
                },
            }
        ]
    ),
)

# Get the existing Route 53 hosted zone
zone = route53.get_zone(name="chsandbox.com")

# Request an ACM certificate for the subdomain
cert = acm.Certificate(
    "streamlit-cert",
    domain_name="pulumi-first.chsandbox.com",
    validation_method="DNS",
)

# Create DNS validation record for ACM certificate
cert_validation_record = route53.Record(
    "cert-validation",
    zone_id=zone.id,
    name=cert.domain_validation_options[0].resource_record_name,
    type=cert.domain_validation_options[0].resource_record_type,
    records=[cert.domain_validation_options[0].resource_record_value],
    ttl=60,
)

# Wait for certificate validation to complete
cert_validation = acm.CertificateValidation(
    "cert-validation-completion",
    certificate_arn=cert.arn,
    validation_record_fqdns=[cert_validation_record.fqdn],
)

# Create security group for the load balancer
alb_security_group = ec2.SecurityGroup(
    "alb-security-group",
    vpc_id=vpc.vpc_id,
    description="Allow HTTP and HTTPS traffic to load balancer",
    ingress=[
        ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=80,
            to_port=80,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow HTTP from anywhere",
        ),
        ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=443,
            to_port=443,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow HTTPS from anywhere",
        ),
    ],
    egress=[
        ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        )
    ],
)

# Create security group for ECS tasks
ecs_security_group = ec2.SecurityGroup(
    "ecs-security-group",
    vpc_id=vpc.vpc_id,
    description="Allow traffic from load balancer to ECS tasks",
    ingress=[
        ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=8501,
            to_port=8501,
            security_groups=[alb_security_group.id],
            description="Allow traffic from ALB",
        )
    ],
    egress=[
        ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all outbound traffic",
        )
    ],
)

# Create Application Load Balancer
alb = lb.LoadBalancer(
    "streamlit-alb",
    load_balancer_type="application",
    subnets=vpc.public_subnet_ids,
    security_groups=[alb_security_group.id],
)

# Create target group for the ECS service
target_group = lb.TargetGroup(
    "streamlit-target-group",
    port=8501,
    protocol="HTTP",
    target_type="ip",
    vpc_id=vpc.vpc_id,
    health_check=lb.TargetGroupHealthCheckArgs(
        enabled=True,
        path="/_stcore/health",  # Use Streamlit's built-in health endpoint
        protocol="HTTP",
        matcher="200",
        interval=30,
        timeout=10,  # Increased timeout for Streamlit app startup
        healthy_threshold=2,
        unhealthy_threshold=3,
    ),
)

# Create HTTPS listener for the load balancer
https_listener = lb.Listener(
    "streamlit-https-listener",
    load_balancer_arn=alb.arn,
    port=443,
    protocol="HTTPS",
    ssl_policy="ELBSecurityPolicy-TLS13-1-2-2021-06",
    certificate_arn=cert.arn,
    default_actions=[
        lb.ListenerDefaultActionArgs(
            type="forward",
            target_group_arn=target_group.arn,
        )
    ],
    opts=pulumi.ResourceOptions(depends_on=[cert_validation]),
)

# Create HTTP listener that redirects to HTTPS
http_listener = lb.Listener(
    "streamlit-http-listener",
    load_balancer_arn=alb.arn,
    port=80,
    protocol="HTTP",
    default_actions=[
        lb.ListenerDefaultActionArgs(
            type="redirect",
            redirect=lb.ListenerDefaultActionRedirectArgs(
                protocol="HTTPS",
                port="443",
                status_code="HTTP_301",
            ),
        )
    ],
)

# Create ECS Fargate service
service = ecs.Service(
    "streamlit-service",
    cluster=cluster.arn,
    desired_count=1,
    launch_type="FARGATE",
    task_definition=task_definition.arn,
    network_configuration=ecs.ServiceNetworkConfigurationArgs(
        assign_public_ip=True,
        subnets=vpc.public_subnet_ids,
        security_groups=[ecs_security_group.id],
    ),
    load_balancers=[
        ecs.ServiceLoadBalancerArgs(
            target_group_arn=target_group.arn,
            container_name="streamlit-container",
            container_port=8501,
        )
    ],
    opts=pulumi.ResourceOptions(depends_on=[https_listener]),
)

# Create DNS A record pointing to the ALB
app_dns_record = route53.Record(
    "app-dns",
    zone_id=zone.id,
    name="pulumi-first.chsandbox.com",
    type="A",
    aliases=[
        route53.RecordAliasArgs(
            name=alb.dns_name,
            zone_id=alb.zone_id,
            evaluate_target_health=True,
        )
    ],
)

# Export outputs
pulumi.export("vpc_id", vpc.vpc_id)
pulumi.export("public_subnet_ids", vpc.public_subnet_ids)
pulumi.export("private_subnet_ids", vpc.private_subnet_ids)
pulumi.export("ecr_repository_url", ecr_repo.repository_url)
pulumi.export("alb_dns_name", alb.dns_name)
pulumi.export("app_url", "https://pulumi-first.chsandbox.com")
pulumi.export("certificate_arn", cert.arn)
