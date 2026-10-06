variable "function_name" {
  type = string
}
variable "source_dir" {
  description = "Folder with the Lambda Python files."
  type        = string
}
variable "table_name" {
  type = string
}
variable "table_arn" {
  type = string
}
variable "model_id" {
  description = "Inference profile eu."
  type        = string
}
variable "foundation_model" {
  type = string
}
variable "bedrock_regions" {
  description = "EU regions the inference profile may route to."
  type        = list(string)
}
variable "log_retention_days" {
  type = number
}