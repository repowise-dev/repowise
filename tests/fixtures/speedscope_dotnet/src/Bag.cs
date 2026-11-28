using System.Collections.Generic;

namespace Shop.Storage
{
    public class Bag<T>
    {
        private readonly List<T> _items = new();

        public int Fill(T item, int times)
        {
            for (var i = 0; i < times; i++)
            {
                _items.Add(item);
            }
            return Weigh();

            int Weigh()
            {
                var w = 0;
                foreach (var x in _items)
                {
                    w += x!.GetHashCode() % 3;
                }
                return w;
            }
        }
    }
}
