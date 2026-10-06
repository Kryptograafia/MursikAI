variable "aws_region" {
  description = "Region for the table, the function and the Bedrock call"
  type        = string
  default     = "eu-north-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "mursik"
}

variable "log_retention_days" {
  description = "CloudWatch retention. Logs hold tool names and errors, never message text."
  type        = number
  default     = 14
}

variable "model_id" {
  description = "EU inference profile passed to the Bedrock Converse API."
  type        = string
  default     = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "foundation_model" {
  description = "The model behind the profile"
  type        = string
  default     = "anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_regions" {
  description = "EU regions the inference profile may route to."
  type        = list(string)
  default = [
    "eu-central-1",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-3",
  ]
}