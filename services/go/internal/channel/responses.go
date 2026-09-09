package channel

// BindingClaimResponse carries the existing one-time confirmation command.
type BindingClaimResponse struct {
	ConfirmationCommand string `json:"confirmation_command"`
}
