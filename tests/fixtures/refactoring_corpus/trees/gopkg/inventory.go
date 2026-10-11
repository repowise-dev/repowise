// Archetype: a Go file that has grown two independent responsibilities.
//
// Splitting it into sibling files in the same package rewrites no import in any
// caller, because Go package members keep their qualified name whichever file
// declares them. That is the one structural refactoring this layer can call
// mechanical, and this fixture is what pins it.

package inventory

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

var ErrNotFound = errors.New("not found")

type Stock struct {
	SKU      string
	OnHand   int
	Reserved int
	Bin      string
}

func NewStock(sku string, onHand int, bin string) *Stock {
	return &Stock{SKU: sku, OnHand: onHand, Bin: strings.ToUpper(bin)}
}

func (s *Stock) Available() int {
	if s.Reserved > s.OnHand {
		return 0
	}
	return s.OnHand - s.Reserved
}

func (s *Stock) Reserve(units int) error {
	if units <= 0 {
		return fmt.Errorf("reserve %d: %w", units, errors.ErrUnsupported)
	}
	if units > s.Available() {
		return fmt.Errorf("reserve %d of %s: %w", units, s.SKU, ErrNotFound)
	}
	s.Reserved += units
	return nil
}

func (s *Stock) Release(units int) {
	if units <= 0 {
		return
	}
	if units > s.Reserved {
		s.Reserved = 0
		return
	}
	s.Reserved -= units
}

func (s *Stock) Receive(units int) {
	if units > 0 {
		s.OnHand += units
	}
}

func StockBySKU(items []*Stock) map[string]*Stock {
	out := make(map[string]*Stock, len(items))
	for _, item := range items {
		if item == nil || item.SKU == "" {
			continue
		}
		out[item.SKU] = item
	}
	return out
}

func TotalAvailable(items []*Stock) int {
	total := 0
	for _, item := range items {
		if item == nil {
			continue
		}
		total += item.Available()
	}
	return total
}

func LowStock(items []*Stock, threshold int) []*Stock {
	var low []*Stock
	for _, item := range items {
		if item == nil {
			continue
		}
		if item.Available() < threshold {
			low = append(low, item)
		}
	}
	sort.Slice(low, func(i, j int) bool { return low[i].SKU < low[j].SKU })
	return low
}

func RebinStock(items []*Stock, from string, to string) int {
	moved := 0
	for _, item := range items {
		if item == nil {
			continue
		}
		if item.Bin == strings.ToUpper(from) {
			item.Bin = strings.ToUpper(to)
			moved++
		}
	}
	return moved
}

type Supplier struct {
	Code     string
	Name     string
	Country  string
	LeadDays int
}

func NewSupplier(code string, name string, country string) *Supplier {
	return &Supplier{Code: strings.ToUpper(code), Name: name, Country: strings.ToUpper(country)}
}

func (s *Supplier) Label() string {
	if s.Name == "" {
		return s.Code
	}
	return fmt.Sprintf("%s (%s)", s.Name, s.Code)
}

func (s *Supplier) Domestic(home string) bool {
	return s.Country == strings.ToUpper(home)
}

func (s *Supplier) Slower(other *Supplier) bool {
	if other == nil {
		return false
	}
	return s.LeadDays > other.LeadDays
}

func SupplierByCode(suppliers []*Supplier) map[string]*Supplier {
	out := make(map[string]*Supplier, len(suppliers))
	for _, supplier := range suppliers {
		if supplier == nil || supplier.Code == "" {
			continue
		}
		out[supplier.Code] = supplier
	}
	return out
}

func FastestSupplier(suppliers []*Supplier) *Supplier {
	var best *Supplier
	for _, supplier := range suppliers {
		if supplier == nil {
			continue
		}
		if best == nil || supplier.Slower(best) == false && best.Slower(supplier) {
			best = supplier
		}
	}
	return best
}

func DomesticSuppliers(suppliers []*Supplier, home string) []*Supplier {
	var out []*Supplier
	for _, supplier := range suppliers {
		if supplier == nil {
			continue
		}
		if supplier.Domestic(home) {
			out = append(out, supplier)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Code < out[j].Code })
	return out
}

func SupplierLabels(suppliers []*Supplier) []string {
	labels := make([]string, 0, len(suppliers))
	for _, supplier := range suppliers {
		if supplier == nil {
			continue
		}
		labels = append(labels, supplier.Label())
	}
	sort.Strings(labels)
	return labels
}

func RenameSupplier(suppliers []*Supplier, code string, name string) error {
	for _, supplier := range suppliers {
		if supplier == nil {
			continue
		}
		if supplier.Code == strings.ToUpper(code) {
			supplier.Name = name
			return nil
		}
	}
	return fmt.Errorf("rename %s: %w", code, ErrNotFound)
}

type PurchaseOrder struct {
	Ref      string
	Supplier string
	Lines    []OrderLine
	State    string
}

type OrderLine struct {
	SKU   string
	Units int
	Price float64
}

func NewPurchaseOrder(ref string, supplier string) *PurchaseOrder {
	return &PurchaseOrder{Ref: strings.ToUpper(ref), Supplier: strings.ToUpper(supplier), State: "draft"}
}

func (o *PurchaseOrder) AddLine(sku string, units int, price float64) error {
	if units <= 0 {
		return fmt.Errorf("add %s: units must be positive", sku)
	}
	if price < 0 {
		return fmt.Errorf("add %s: price must not be negative", sku)
	}
	for index := range o.Lines {
		if o.Lines[index].SKU == sku {
			o.Lines[index].Units += units
			return nil
		}
	}
	o.Lines = append(o.Lines, OrderLine{SKU: sku, Units: units, Price: price})
	return nil
}

func (o *PurchaseOrder) Value() float64 {
	total := 0.0
	for _, line := range o.Lines {
		total += float64(line.Units) * line.Price
	}
	return total
}

func (o *PurchaseOrder) Submit() error {
	if o.State != "draft" {
		return fmt.Errorf("submit %s: state is %s", o.Ref, o.State)
	}
	if len(o.Lines) == 0 {
		return fmt.Errorf("submit %s: no lines", o.Ref)
	}
	o.State = "submitted"
	return nil
}

func (o *PurchaseOrder) Cancel(reason string) error {
	if o.State == "received" {
		return fmt.Errorf("cancel %s: already received", o.Ref)
	}
	if strings.TrimSpace(reason) == "" {
		return fmt.Errorf("cancel %s: reason required", o.Ref)
	}
	o.State = "cancelled"
	return nil
}

func OrdersBySupplier(orders []*PurchaseOrder) map[string][]*PurchaseOrder {
	out := make(map[string][]*PurchaseOrder)
	for _, order := range orders {
		if order == nil || order.Supplier == "" {
			continue
		}
		out[order.Supplier] = append(out[order.Supplier], order)
	}
	for supplier := range out {
		group := out[supplier]
		sort.Slice(group, func(i, j int) bool { return group[i].Ref < group[j].Ref })
	}
	return out
}

func OpenOrders(orders []*PurchaseOrder) []*PurchaseOrder {
	var open []*PurchaseOrder
	for _, order := range orders {
		if order == nil {
			continue
		}
		if order.State == "draft" || order.State == "submitted" {
			open = append(open, order)
		}
	}
	sort.Slice(open, func(i, j int) bool { return open[i].Ref < open[j].Ref })
	return open
}

func OrderBacklogValue(orders []*PurchaseOrder) float64 {
	total := 0.0
	for _, order := range OpenOrders(orders) {
		total += order.Value()
	}
	return total
}

func FindOrder(orders []*PurchaseOrder, ref string) (*PurchaseOrder, error) {
	for _, order := range orders {
		if order == nil {
			continue
		}
		if order.Ref == strings.ToUpper(ref) {
			return order, nil
		}
	}
	return nil, fmt.Errorf("order %s: %w", ref, ErrNotFound)
}

func OrderRefs(orders []*PurchaseOrder) []string {
	refs := make([]string, 0, len(orders))
	for _, order := range orders {
		if order == nil {
			continue
		}
		refs = append(refs, order.Ref)
	}
	sort.Strings(refs)
	return refs
}

func ReceiveOrder(order *PurchaseOrder, stock []*Stock) (int, error) {
	if order == nil {
		return 0, fmt.Errorf("receive: %w", ErrNotFound)
	}
	if order.State != "submitted" {
		return 0, fmt.Errorf("receive %s: state is %s", order.Ref, order.State)
	}
	index := StockBySKU(stock)
	received := 0
	for _, line := range order.Lines {
		item, ok := index[line.SKU]
		if !ok {
			continue
		}
		item.Receive(line.Units)
		received += line.Units
	}
	order.State = "received"
	return received, nil
}

func OrderSummary(order *PurchaseOrder) string {
	if order == nil {
		return ""
	}
	units := 0
	for _, line := range order.Lines {
		units += line.Units
	}
	return fmt.Sprintf("%s %s units=%d value=%.2f", order.Ref, order.State, units, order.Value())
}
